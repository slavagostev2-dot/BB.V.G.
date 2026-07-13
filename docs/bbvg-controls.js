'use strict';

(()=>{
  const hiddenWheels=new Set();
  const baseActiveWheels=activeWheels;
  const baseRenderHome=renderHome;
  const baseRenderProfile=renderProfile;

  activeWheels=function(){
    return baseActiveWheels().filter(wheel=>!hiddenWheels.has(wheelKey(wheel)));
  };

  function enhanceWheelCards(){
    document.querySelectorAll('.wheel-card').forEach(card=>{
      const actions=card.querySelector('.actions');
      const join=card.querySelector('[data-action="join"]');
      if(!actions||!join||actions.querySelector('[data-action="hide-wheel"]'))return;
      const button=document.createElement('button');
      button.type='button';
      button.className='button secondary';
      button.dataset.action='hide-wheel';
      button.dataset.id=String(join.dataset.id||'').toLowerCase();
      button.textContent='Неактивное';
      actions.append(button);
    });
  }

  function appendHiddenControl(){
    if(!hiddenWheels.size)return;
    const settings=document.querySelector('#page-profile .section:last-child .card');
    if(!settings||settings.querySelector('[data-action="restore-hidden"]'))return;
    const row=document.createElement('div');
    row.className='setting';
    row.innerHTML=`<div class="setting-copy"><strong>Скрытые колёса</strong><small>Скрыто только у вас: ${hiddenWheels.size}</small></div><button class="button secondary" type="button" data-action="restore-hidden">Показать</button>`;
    settings.append(row);
  }

  renderHome=function(){
    baseRenderHome();
    enhanceWheelCards();
  };

  renderProfile=function(){
    baseRenderProfile();
    enhanceWheelCards();
    appendHiddenControl();
  };

  document.addEventListener('click',event=>{
    const hideButton=event.target.closest('[data-action="hide-wheel"]');
    if(hideButton){
      const key=String(hideButton.dataset.id||'').toLowerCase();
      if(key){
        hiddenWheels.add(key);
        store.set('hiddenWheels',[...hiddenWheels]);
        toast('Колесо скрыто только у вас');
        haptic('warning');
        renderHome();
        renderProfile();
      }
      return;
    }
    const restoreButton=event.target.closest('[data-action="restore-hidden"]');
    if(restoreButton){
      hiddenWheels.clear();
      store.set('hiddenWheels',[]);
      toast('Скрытые колёса снова показаны');
      renderHome();
      renderProfile();
    }
  });

  store.get('hiddenWheels',[]).then(values=>{
    if(Array.isArray(values)){
      values.forEach(value=>{
        const key=String(value||'').toLowerCase();
        if(key)hiddenWheels.add(key);
      });
    }
    if(app.lastSync){
      renderHome();
      renderProfile();
    }
  });
})();
