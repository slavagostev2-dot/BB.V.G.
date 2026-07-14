'use strict';

(()=>{
  const hiddenWheels=new Set();
  let wheelFilter='all';
  let sourceFilter='all';
  let showAllRanks=false;
  let showAllMarks=false;
  const baseActiveWheels=activeWheels;

  activeWheels=function(){
    return baseActiveWheels().filter(wheel=>!hiddenWheels.has(wheelKey(wheel)));
  };

  const filterIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M7 12h10m-7 6h4"/></svg>';

  function metricWithDelta(icon,value,label){
    return `<article class="metric"><span class="metric-icon">${icon}</span><strong>${value}</strong><span>${label}</span></article>`;
  }

  function enhancedWheelCard(wheel){
    const id=wheelKey(wheel);
    const marked=isJoined(wheel);
    const url=safeUrl(wheel.url);
    const source=String(wheel.source||'неизвестно');
    return `<article class="card wheel-card">
      <div class="wheel-top">
        <div class="wheel-main"><span class="wheel-avatar">BB</span><div class="wheel-id"><h3>${esc(wheel.identifier||wheel.key)}</h3><small>@${esc(source)}</small></div></div>
        <span class="badge ${marked?'mine':''}">${marked?'Отмечено':'Активно'}</span>
      </div>
      <div class="countdown-row"><span>До прокрутки</span><strong class="countdown" ${wheel.deadlineObj?`data-deadline="${esc(wheel.deadlineObj.toISOString())}"`:''}>${esc(wheel.deadlineObj?timeLeft(wheel.deadlineObj):'Время не определено')}</strong></div>
      <div class="actions">
        ${url?`<button class="button primary" data-action="open-url" data-url="${esc(url)}">Открыть колесо</button>`:'<button class="button primary" disabled>Ссылка недоступна</button>'}
        <button class="button ${marked?'success':'secondary'}" data-action="join" data-id="${esc(id)}">${marked?'Участие отмечено':'Я участвую'}</button>
        <button class="button secondary" data-action="hide-wheel" data-id="${esc(id)}">Неактивное</button>
      </div>
    </article>`;
  }

  renderHome=function(){
    const all=activeWheels();
    const visible=wheelFilter==='mine'?all.filter(isJoined):all;
    const mine=all.filter(isJoined).length;
    $('#page-home').innerHTML=`
      <article class="overview">
        <div class="system-line"><span class="system-dot"></span>Система работает</div>
        <div class="overview-copy">Мониторинг в реальном времени</div>
        <div class="metrics">
          <article class="metric"><strong>${all.length}</strong><span>Активные колёса</span></article>
          <article class="metric"><strong>${mine}</strong><span>Моих отметок</span></article>
        </div>
      </article>
      <section class="section">
        <div class="section-head">
          <div class="section-tools"><h2 class="section-title">Активные колёса</h2><span class="count-pill">${all.length}</span></div>
          <button class="filter-button" data-action="wheel-filter">Фильтр ${filterIcon}</button>
        </div>
        <div>${visible.map(enhancedWheelCard).join('')||'<div class="empty">Сейчас действующих колёс нет.</div>'}</div>
      </section>`;
    updateTimers();
  };

  renderStats=function(){
    const total=totals(app.days);
    const confirmed=Number(adminRatingsActive()?(total.admin_confirmed_wheels||0):(total.activation_sent||0));
    const ranks=ranking();
    const shown=showAllRanks?ranks:ranks.slice(0,5);
    $('#page-stats').innerHTML=`
      <div class="periods">${[1,7,30].map(days=>`<button class="chip ${app.days===days?'active':''}" type="button" data-days="${days}">${days===1?'Сегодня':`${days} дней`}</button>`).join('')}</div>
      <div class="stats-grid">
        ${metricWithDelta(iconSvg.scan,compact(total.checks),'Проверок источников')}
        ${metricWithDelta(iconSvg.message,compact(total.messages_scanned),'Просмотрено сообщений')}
        ${metricWithDelta(iconSvg.wheel,num(total.wheel_posts),'Постов с колёсами')}
        ${metricWithDelta(iconSvg.check,num(confirmed),'Активные колёса')}
      </div>
      ${app.days===1?'':`<section class="section"><article class="card chart-card"><div class="section-head"><h2 class="section-title">Активные колёса за ${app.days} дней</h2><span class="chart-total">${num(confirmed)}</span></div>${chart(app.days)}</article></section>`}
      <section class="section"><div class="section-head"><h2 class="section-title">Топ источников</h2>${ranks.length>5?`<button class="text-button" data-action="toggle-ranks">${showAllRanks?'Свернуть':'Смотреть все'}</button>`:''}</div><article class="card">${shown.map(rankRow).join('')||'<div class="empty">Рейтинг ещё формируется.</div>'}</article></section>`;
  };

  function visibleSources(){
    const list=[...new Map([...app.data.primary,...app.data.nightly].map(item=>[item.toLowerCase(),item])).values()];
    const query=app.query.trim().toLowerCase();
    return list.filter(name=>(!query||name.toLowerCase().includes(query))&&(sourceFilter!=='wheels'||Number(sourceStats(name)?.wheel_posts||0)>0));
  }
  window.bbvgVisibleSources=visibleSources;

  renderSources=function(){
    const rows=visibleSources();
    const overview=sourceOverview();
    $('#page-sources').innerHTML=`
      <div class="stats-grid"><article class="metric"><strong>${overview.total}</strong><span>Всего источников</span></article><article class="metric"><strong>${overview.checked}</strong><span>Проверено сейчас</span></article><article class="metric"><strong>${overview.reachable}</strong><span>Доступно</span></article><article class="metric"><strong>${overview.unavailable}</strong><span>Недоступно</span></article></div>
      <form id="sourceRequestForm" class="source-form">
        <div class="source-form-head"><span class="source-form-icon">${iconSvg.link}</span><h2>Предложить источник</h2></div>
        <p>Отправьте username канала или чата для проверки модератором.</p>
        <div class="form-row"><input id="sourceRequestInput" class="input" type="text" autocomplete="off" maxlength="33" placeholder="telegram.me/имя"><button class="form-button" type="submit">Отправить</button></div>
      </form>
      <div class="search-row"><input id="sourceSearch" class="search" type="search" autocomplete="off" placeholder="Поиск источника" value="${esc(app.query)}"><button class="square-button" data-action="source-filter" aria-label="Фильтр">${filterIcon}</button></div>
      <article class="card">${rows.slice(0,100).map(sourceRow).join('')||'<div class="empty">Источники не найдены.</div>'}</article>
      <article class="card" style="margin-top:10px"><p class="muted" style="margin:0">После отправки заявки администратор получит запрос на модерацию. О результате бот пришлёт уведомление.</p></article>`;
  };

  renderProfile=function(){
    const user=currentUser();
    const name=user?[user.first_name,user.last_name].filter(Boolean).join(' '):'Пользователь';
    const photo=safeUrl(user?.photo_url)||'icon.svg';
    const marks=activeWheels().filter(isJoined);
    const shown=showAllMarks?marks:marks.slice(0,3);
    const active=activeWheels();
    $('#page-profile').innerHTML=`
      <article class="card profile-head">
        <img src="${esc(photo)}" alt=""><div class="profile-copy"><strong>${esc(name||'Пользователь')}</strong><span>${user?.username?`@${esc(user.username)}`:'Telegram Mini App'}</span></div>
        <div class="profile-stats"><div class="profile-stat"><strong>${app.participationHistory.size}</strong><span>Всего участий</span></div><div class="profile-stat"><strong>${active.length}</strong><span>Активные колёса</span></div><div class="profile-stat"><strong>${marks.length}</strong><span>Моих отметок</span></div></div>
      </article>
      <section class="section"><div class="section-head"><div class="section-tools"><h2 class="section-title">Мои отметки</h2><span class="count-pill">${marks.length}</span></div>${marks.length>3?`<button class="text-button" data-action="toggle-marks">${showAllMarks?'Свернуть':'Смотреть все'}</button>`:''}</div><div>${shown.map(enhancedWheelCard).join('')||'<div class="empty">Вы пока не отметили участие в действующих колёсах.</div>'}</div></section>
      <section id="profileSettings" class="section profile-settings"><h2 class="section-title">Настройки</h2><article class="card">
        <div class="setting"><div class="setting-copy"><strong>Автообновление</strong><small>Обновлять данные автоматически</small></div><button class="switch ${app.settings.autoRefresh?'on':''}" data-setting="autoRefresh"></button></div>
        <div class="setting"><div class="setting-copy"><strong>Тактильный отклик</strong><small>Вибрация при действиях</small></div><button class="switch ${app.settings.haptics?'on':''}" data-setting="haptics"></button></div>
        <div class="setting"><div class="setting-copy"><strong>Светлая тема</strong><small>Светлый фон и тёмный текст</small></div><button class="switch ${app.settings.lightTheme?'on':''}" data-setting="lightTheme" aria-label="Светлая тема" aria-pressed="${app.settings.lightTheme}"></button></div>
        <div class="setting"><div class="setting-copy"><strong>Уведомления</strong><small>Обычные сообщения и настройки по роли</small></div><button class="button secondary" data-action="notifications">Открыть</button></div>
        <div class="setting"><div class="setting-copy"><strong>Версия приложения</strong><small>${BRAND}</small></div><span class="row-value">${VERSION}</span></div>
      </article></section>`;
    updateTimers();
  };

  function showOptions(title,buttons){
    showDialog(`<h2>${title}</h2><div class="dialog-list">${buttons.map(item=>`<button class="dialog-option ${item.active?'active':''}" data-ui-choice="${item.value}">${item.label}</button>`).join('')}</div>`);
  }

  document.addEventListener('click',event=>{
    const action=event.target.closest('[data-action]')?.dataset.action;
    if(action==='hide-wheel'){
      const key=String(event.target.closest('[data-action]').dataset.id||'').toLowerCase();
      if(key){hiddenWheels.add(key);store.set('hiddenWheels',[...hiddenWheels]);toast('Колесо скрыто только у вас');renderHome();renderProfile()}
      return;
    }
    if(action==='wheel-filter'){
      showOptions('Фильтр колёс',[{value:'wheel:all',label:'Все актуальные',active:wheelFilter==='all'},{value:'wheel:mine',label:'Только мои отметки',active:wheelFilter==='mine'}]);
      return;
    }
    if(action==='source-filter'){
      showOptions('Фильтр источников',[{value:'source:all',label:'Все источники',active:sourceFilter==='all'},{value:'source:wheels',label:'Только с найденными колёсами',active:sourceFilter==='wheels'}]);
      return;
    }
    if(action==='toggle-ranks'){showAllRanks=!showAllRanks;renderStats();return}
    if(action==='toggle-marks'){showAllMarks=!showAllMarks;renderProfile();return}
    const choice=event.target.closest('[data-ui-choice]')?.dataset.uiChoice;
    if(choice){const [type,value]=choice.split(':');if(type==='wheel'){wheelFilter=value;renderHome()}if(type==='source'){sourceFilter=value;renderSources()}haptic('selection');closeDialog()}
  },true);

  store.get('hiddenWheels',[]).then(values=>{
    if(Array.isArray(values))values.forEach(value=>{const key=String(value||'').toLowerCase();if(key)hiddenWheels.add(key)});
    if(app.lastSync){renderHome();renderStats();renderSources();renderProfile()}
  });
})();
