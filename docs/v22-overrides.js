'use strict';

(()=>{
  const RELEASE='5.11.0';
  const registryRows=new Map();
  let registrySummary=null;
  const filterIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M7 12h10m-7 6h4"/></svg>';

  const baseSourceStats=sourceStats;
  const baseSourceHealth=sourceHealth;
  const baseRenderProfile=renderProfile;

  function registryRow(name){
    return registryRows.get(String(name||'').toLowerCase())||null;
  }

  sourceStats=function(name){
    const base=baseSourceStats(name)||{};
    const row=registryRow(name);
    return row?{...base,wheel_posts:Number(row.wheel_posts||base.wheel_posts||0),quality_score:Number(row.quality_score||base.quality_score||0),admin_confirmed_wheels:Number(row.admin_confirmed_wheels||base.admin_confirmed_wheels||0),admin_rejected_wheels:Number(row.admin_rejected_wheels||base.admin_rejected_wheels||0)}:base;
  };

  sourceHealth=function(name){
    const base=baseSourceHealth(name)||{};
    const row=registryRow(name);
    if(!row)return base;
    return {
      ...base,
      status:row.status==='available'?'ok':(row.status==='pending'?'pending':String(row.raw_status||base.status||'unavailable')),
      failure_reason:row.status==='available'?'':String(row.reason||base.failure_reason||base.last_error||''),
      last_checked_at:row.last_checked_at||base.last_checked_at||null
    };
  };

  sourceOverview=function(){
    if(registrySummary)return {...registrySummary};
    const total=new Set([...app.data.primary,...app.data.nightly].map(item=>item.toLowerCase())).size;
    const checked=[...app.data.primary,...app.data.nightly].filter(name=>Boolean(sourceHealth(name)?.last_checked_at)).length;
    const reachable=[...app.data.primary,...app.data.nightly].filter(name=>sourceHealth(name)?.status==='ok').length;
    return {total,primary:app.data.primary.length,nightly:app.data.nightly.length,checked,available:reachable,reachable,unavailable:Math.max(0,checked-reachable),pending:Math.max(0,total-checked)};
  };

  sourceRow=function(name){
    const stats=sourceStats(name);
    const health=sourceHealth(name);
    const row=registryRow(name);
    const wheels=Number(stats?.wheel_posts||0);
    const publicStatus=row?.status||(
      health?.status==='ok'?'available':health?.last_checked_at?'unavailable':'pending'
    );
    const labels={available:'Доступен',unavailable:'Недоступен',pending:'Ожидает проверки'};
    const reason=publicStatus==='available'?'':String(row?.reason||health?.failure_reason||health?.last_error||'');
    const status=labels[publicStatus]||String(health?.status||'Ожидает проверки');
    return `<button class="source-row" type="button" data-action="source-info" data-source="${esc(name)}"><span class="source-mark">${esc(initials(name))}</span><span class="row-copy"><strong>@${esc(name)}</strong><small>${esc(status)}${reason?` · ${esc(reason)}`:''}${wheels?` · колёс: ${num(wheels)}`:''}</small></span><span class="source-status">${publicStatus==='available'?'✓':publicStatus==='pending'?'…':'!'}</span></button>`;
  };

  renderSources=function(){
    const rows=typeof window.bbvgVisibleSources==='function'?window.bbvgVisibleSources():filteredSources();
    const overview=sourceOverview();
    const total=Number(overview.total||0);
    const checked=Number(overview.checked||0);
    const available=Number(overview.available??overview.reachable??0);
    const unavailable=Number(overview.unavailable||0);
    const pending=Number(overview.pending??Math.max(0,total-checked));
    $('#page-sources').innerHTML=`
      <form id="sourceRequestForm" class="source-form">
        <div class="source-form-head"><span class="source-form-icon">${iconSvg.link}</span><h2>Предложить источник</h2></div>
        <p>Отправьте username канала или чата для проверки модератором.</p>
        <div class="form-row"><input id="sourceRequestInput" class="input" type="text" autocomplete="off" maxlength="33" placeholder="telegram.me/имя"><button class="form-button" type="submit">Отправить</button></div>
      </form>
      <div class="source-count-note"><span aria-hidden="true"></span>Источников в едином реестре: <strong>${num(total)}</strong></div>
      <article class="card" style="margin-bottom:10px"><div class="setting"><div class="setting-copy"><strong>Состояние базы</strong><small>Проверено ${num(checked)} · доступно ${num(available)} · недоступно ${num(unavailable)} · ожидает ${num(pending)}</small></div></div></article>
      <div class="search-row"><input id="sourceSearch" class="search" type="search" autocomplete="off" placeholder="Поиск источника" value="${esc(app.query)}"><button class="square-button" data-action="source-filter" aria-label="Фильтр">${filterIcon}</button></div>
      <article class="card">${rows.slice(0,100).map(sourceRow).join('')||'<div class="empty">Источники не найдены.</div>'}</article>
      <article class="card" style="margin-top:10px"><p class="muted" style="margin:0">Основной и ночной режимы входят в один реестр. Каждый источник отображается один раз с фактической причиной состояния.</p></article>`;
  };

  renderProfile=function(){
    baseRenderProfile();
    $$('#profileSettings .setting').forEach(item=>{
      if(item.textContent.includes('Версия приложения')){
        const value=item.querySelector('.row-value,.muted');
        if(value)value.textContent=RELEASE;
      }
    });
  };

  async function loadRegistry(){
    try{
      const value=await fetchOne('source_registry.json');
      const rows=Array.isArray(value?.sources)?value.sources:[];
      registryRows.clear();
      rows.forEach(row=>{
        const username=String(row?.username||'').trim();
        if(username)registryRows.set(username.toLowerCase(),row);
      });
      registrySummary=value?.summary&&typeof value.summary==='object'?value.summary:null;
      if(rows.length){
        app.data.primary=rows.filter(row=>row?.tier==='primary').map(row=>String(row.username));
        app.data.nightly=rows.filter(row=>row?.tier==='nightly').map(row=>String(row.username));
      }
      if(app.lastSync){renderSources();renderStats();renderProfile()}
    }catch(error){
      console.warn('BB V.G. source registry:',error);
    }
  }

  loadRegistry();
})();
