'use strict';

(()=>{
  const apiUrl=String(window.BB_CONFIG?.privateStateApiUrl||'').replace(/\/$/,'');
  const initData=String(tg?.initData||'');
  const listeners=new Set();
  const state={
    ready:false,
    hiddenWheels:new Set(),
    onChange(listener){
      if(typeof listener!=='function')return;
      listeners.add(listener);
      if(this.ready)listener(this);
    },
    emit(){
      listeners.forEach(listener=>{try{listener(this)}catch(error){console.warn(error)}});
    },
    async request(path,{method='GET',body}={}){
      if(!apiUrl||!initData)throw new Error('Приватное хранилище пока недоступно');
      const response=await fetch(`${apiUrl}${path}`,{
        method,
        cache:'no-store',
        headers:{
          Accept:'application/json',
          'Content-Type':'application/json',
          'X-Telegram-Init-Data':initData
        },
        body:body===undefined?undefined:JSON.stringify(body)
      });
      const payload=await response.json().catch(()=>({}));
      if(!response.ok||payload?.ok===false){
        throw new Error(String(payload?.error||`HTTP ${response.status}`));
      }
      return payload;
    },
    async setHidden(key,hidden=true){
      const normalized=String(key||'').toLowerCase();
      if(!normalized)return;
      const before=new Set(this.hiddenWheels);
      if(hidden)this.hiddenWheels.add(normalized);else this.hiddenWheels.delete(normalized);
      this.emit();
      try{
        await this.request('/v1/me/hidden',{method:'PUT',body:{wheel_key:normalized,hidden}});
      }catch(error){
        this.hiddenWheels=before;
        this.emit();
        toast('Не удалось сохранить скрытие');
        haptic('error');
        console.warn(error);
      }
    },
    async saveSettings(settings){
      try{
        await this.request('/v1/me/settings',{method:'PUT',body:{settings}});
      }catch(error){
        console.warn('Private settings:',error);
      }
    }
  };
  window.BBPrivateState=state;

  const baseToggleJoined=toggleJoined;
  toggleJoined=async function(id){
    const key=String(id||'').toLowerCase();
    if(!key)return;
    const joined=!app.joined.has(key);
    await baseToggleJoined(key);
    if(!apiUrl||!initData)return;
    try{
      await state.request('/v1/me/participation',{method:'PUT',body:{wheel_key:key,joined}});
    }catch(error){
      await baseToggleJoined(key);
      toast('Не удалось сохранить отметку');
      haptic('error');
      console.warn(error);
    }
  };

  submitSourceRequest=async function(raw){
    const username=normalizeSource(raw);
    if(!/^[A-Za-z][A-Za-z0-9_]{3,31}$/.test(username)){
      toast('Введите корректный username канала');
      haptic('warning');
      return;
    }
    if(knownSource(username)){
      toast('Этот источник уже проверяется');
      return;
    }
    if(!apiUrl||!initData){
      toast('Откройте приложение внутри Telegram');
      haptic('warning');
      return;
    }
    const button=document.querySelector('#sourceRequestForm .form-button');
    if(button){button.disabled=true;button.textContent='Проверяю…'}
    try{
      const result=await state.request('/v1/source-requests',{method:'POST',body:{source:username}});
      const input=document.querySelector('#sourceRequestInput');
      if(input)input.value='';
      toast(result.duplicate?'Заявка уже ожидает решения':'Заявка принята');
      haptic('success');
    }catch(error){
      toast(error.message||'Не удалось отправить заявку');
      haptic('error');
    }finally{
      if(button){button.disabled=false;button.textContent='Отправить'}
    }
  };

  const baseStoreSet=store.set.bind(store);
  store.set=function(key,value){
    baseStoreSet(key,value);
    if(key==='settings'&&state.ready)state.saveSettings(value);
  };

  async function waitForInitialLoad(){
    for(let attempt=0;attempt<100;attempt+=1){
      const shell=document.querySelector('#app');
      if(app.lastSync||shell?.hidden===false)return;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
  }

  async function migrateLocal(remote){
    const localJoined=new Set([...app.joined].map(value=>String(value).toLowerCase()));
    const localHistory=new Set([...app.participationHistory].map(value=>String(value).toLowerCase()));
    const localHiddenValues=await store.get('hiddenWheels',[]);
    const localHidden=new Set(
      Array.isArray(localHiddenValues)
        ?localHiddenValues.map(value=>String(value).toLowerCase()).filter(Boolean)
        :[]
    );
    const remoteJoined=new Set(Array.isArray(remote.joined)?remote.joined.map(value=>String(value).toLowerCase()):[]);
    const remoteHistory=new Set(
      Array.isArray(remote.participationHistory)
        ?remote.participationHistory.map(value=>String(value).toLowerCase())
        :[]
    );
    const remoteHidden=new Set(
      Array.isArray(remote.hiddenWheels)
        ?remote.hiddenWheels.map(value=>String(value).toLowerCase())
        :[]
    );
    const jobs=[];
    localJoined.forEach(key=>{
      if(!remoteJoined.has(key)){
        remoteJoined.add(key);
        remoteHistory.add(key);
        jobs.push(state.request('/v1/me/participation',{method:'PUT',body:{wheel_key:key,joined:true}}));
      }
    });
    localHistory.forEach(key=>{
      if(!remoteHistory.has(key)){
        remoteHistory.add(key);
        jobs.push((async()=>{
          await state.request('/v1/me/participation',{method:'PUT',body:{wheel_key:key,joined:true}});
          await state.request('/v1/me/participation',{method:'PUT',body:{wheel_key:key,joined:false}});
        })());
      }
    });
    localHidden.forEach(key=>{
      if(!remoteHidden.has(key)){
        remoteHidden.add(key);
        jobs.push(state.request('/v1/me/hidden',{method:'PUT',body:{wheel_key:key,hidden:true}}));
      }
    });
    await Promise.allSettled(jobs);
    return {joined:remoteJoined,history:remoteHistory,hidden:remoteHidden};
  }

  async function hydrate(){
    if(!apiUrl||!initData)return;
    try{
      const sessionPromise=state.request('/v1/session',{method:'POST'});
      await waitForInitialLoad();
      const payload=await sessionPromise;
      const remote=payload?.state||{};
      const merged=await migrateLocal(remote);
      app.joined=merged.joined;
      app.participationHistory=merged.history;
      app.joined.forEach(value=>app.participationHistory.add(value));
      state.hiddenWheels=merged.hidden;
      if(remote.settings&&typeof remote.settings==='object'&&Object.keys(remote.settings).length){
        app.settings={...app.settings,...remote.settings};
        baseStoreSet('settings',app.settings);
        applyTheme();
      }else{
        await state.saveSettings(app.settings);
      }
      state.ready=true;
      state.emit();
      renderAll();
    }catch(error){
      console.warn('Private state session:',error);
      toast('Личные данные временно не синхронизированы');
    }
  }

  hydrate();
})();
