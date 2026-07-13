'use strict';

(()=>{
  let userNotifications=true;
  const previousActiveWheels=activeWheels;

  const statusMark=status=>({
    available:'🟢',
    unavailable:'🔴',
    not_checked:'🟡',
    excluded:'⚫'
  }[status]||'⚪');

  const registryRows=()=>Object.entries(app.data.registry?.sources||{})
    .map(([source,row])=>({source,...(row||{})}))
    .sort((a,b)=>a.source.localeCompare(b.source,'ru'));

  const ratingRows=()=>Array.isArray(app.data.reputation?.ranking)
    ?app.data.reputation.ranking.filter(Boolean)
    :[];

  function reputationFor(source){
    const rows=app.data.reputation?.sources||{};
    const key=Object.keys(rows).find(name=>name.toLowerCase()===String(source).toLowerCase());
    return key?rows[key]:{};
  }

  function registryFor(source){
    const rows=app.data.registry?.sources||{};
    const key=Object.keys(rows).find(name=>name.toLowerCase()===String(source).toLowerCase());
    return key?rows[key]:{};
  }

  loadData=async function(quiet=false){
    if(app.loading)return;
    app.loading=true;
    $('#refreshButton').classList.add('loading');
    try{
      const [state,stats,registry,reputation,primaryText,nightlyText]=await Promise.all([
        fetchOne('state.json'),
        fetchOne('source_stats.json'),
        fetchOne('source_registry.json').catch(()=>({summary:{},sources:{}})),
        fetchOne('source_reputation.json').catch(()=>({ranking:[],sources:{},wheels:{}})),
        fetchOne('public_sources.txt','text'),
        fetchOne('source_catalog.txt','text')
      ]);
      app.data={
        state,
        stats,
        registry,
        reputation,
        primary:parseList(primaryText),
        nightly:parseList(nightlyText)
      };
      app.lastSync=new Date();
      renderAll();
      if(!quiet){toast('Данные обновлены');haptic('success')}
    }catch(error){
      console.error(error);
      toast('Не удалось обновить данные');
      haptic('error');
      if(!app.lastSync)renderFatal();
    }finally{
      app.loading=false;
      $('#refreshButton').classList.remove('loading');
      $('#app').hidden=false;
      requestAnimationFrame(()=>$('#splash').classList.add('hidden'));
    }
  };

  activeWheels=function(){
    return previousActiveWheels().filter(wheel=>String(wheel.admin_verdict||'')==='active');
  };

  ranking=function(){
    return ratingRows().map(row=>({
      source:String(row.source||''),
      score:Number(row.score||0),
      wheels:Number(row.confirmed_wheels||0)+Number(row.inactive_wheels||0),
      activations:Number(row.confirmed_wheels||0),
      inactive:Number(row.inactive_wheels||0),
      success:Number(row.success_rate||0),
      trend:Number(row.trend||0),
      place:Number(row.place||0)
    }));
  };

  rankRow=function(item,index){
    const place=item.place||index+1;
    const trend=item.trend>0?`+${item.trend}`:String(item.trend||0);
    return `<button class="rank-row" type="button" data-action="source-info" data-source="${esc(item.source)}">
      <span class="rank-num">${place}</span>
      <span class="source-mark">${esc(initials(item.source))}</span>
      <span class="row-copy"><strong>@${esc(item.source)}</strong><small>Подтверждено ${num(item.activations)} · неактивных ${num(item.inactive)} · успех ${item.success}%</small></span>
      <span class="rating-value"><b>${item.score>0?'+':''}${num(item.score)}</b><small>${trend}</small></span>
    </button>`;
  };

  sourceRow=function(nameOrRow){
    const row=typeof nameOrRow==='string'?{source:nameOrRow,...registryFor(nameOrRow)}:nameOrRow;
    const source=String(row.source||'');
    return `<button class="source-row" type="button" data-action="source-info" data-source="${esc(source)}">
      <span class="source-mark">${esc(initials(source))}</span>
      <span class="row-copy"><strong>@${esc(source)}</strong><small>${esc(row.reason||'Результат проверки ещё не записан')}</small></span>
      <span class="source-status ${esc(row.status||'not_checked')}">${statusMark(row.status)} ${esc(row.status_label||'Ожидает')}</span>
    </button>`;
  };

  renderStats=function(){
    const total=totals(app.days);
    const ranks=ranking().slice(0,5);
    const summary=app.data.registry?.summary||{};
    $('#page-stats').innerHTML=`
      <h1 class="page-title">Статистика</h1>
      <p class="page-subtitle">Работа мониторинга и источников</p>
      <div class="periods">${[1,7,30].map(days=>`<button class="chip ${app.days===days?'active':''}" type="button" data-days="${days}">${days===1?'Сегодня':`${days} дней`}</button>`).join('')}</div>
      <div class="stats-grid">
        ${metricCard(iconSvg.scan,compact(total.checks),'Проверок источников')}
        ${metricCard(iconSvg.message,compact(total.messages_scanned),'Просмотрено сообщений')}
        ${metricCard(iconSvg.wheel,num(total.wheel_posts),'Публикаций с колёсами')}
        ${metricCard(iconSvg.check,num(summary.available_sources),'Доступных источников')}
      </div>
      <section class="section"><article class="card chart-card"><div class="section-head"><h2 class="section-title">Активность</h2></div>${chart(app.days)}</article></section>
      <section class="section">
        <div class="section-head"><h2 class="section-title">Рейтинг источников</h2><button class="text-button" type="button" data-route="rating">Открыть полностью</button></div>
        <article class="card">${ranks.map(rankRow).join('')||'<div class="empty">Рейтинг сформируется после решений администратора.</div>'}</article>
      </section>`;
  };

  renderSources=function(){
    const summary=app.data.registry?.summary||{};
    const query=app.query.trim().toLowerCase();
    const rows=registryRows().filter(row=>!query||row.source.toLowerCase().includes(query));
    $('#page-sources').innerHTML=`
      <h1 class="page-title">Источники</h1>
      <p class="page-subtitle">Единый список без внутренних категорий</p>
      <div class="source-summary-grid">
        <article><strong>${num(summary.total_sources||rows.length)}</strong><span>Всего</span></article>
        <article><strong>${num(summary.checked_sources)}</strong><span>Проверено</span></article>
        <article><strong>${num(summary.available_sources)}</strong><span>Доступно</span></article>
        <article><strong>${num(summary.unavailable_sources)}</strong><span>Недоступно</span></article>
      </div>
      <form id="sourceRequestForm" class="source-form">
        <div class="source-form-head"><span class="source-form-icon">${iconSvg.link}</span><h2>Предложить источник</h2></div>
        <p>Отправьте username канала или чата. Администратор получит единый запрос на модерацию.</p>
        <div class="form-row"><input id="sourceRequestInput" class="input" type="text" autocomplete="off" maxlength="33" placeholder="https://t.me/имя_источника"><button class="form-button" type="submit">Отправить</button></div>
      </form>
      <input id="sourceSearch" class="search" type="search" autocomplete="off" placeholder="Поиск источника" value="${esc(app.query)}">
      <article class="card source-list">${rows.map(sourceRow).join('')||'<div class="empty">Источники не найдены.</div>'}</article>`;
  };

  renderRating=function(){
    const rows=ranking();
    $('#page-rating').innerHTML=`
      <h1 class="page-title">Рейтинг источников</h1>
      <p class="page-subtitle">Административное решение имеет наибольший вес</p>
      <article class="rating-legend card">
        <span><b>+40</b> подтверждено администратором</span>
        <span><b>−45</b> неактивная находка</span>
        <span><b>+8</b> первоисточник</span>
        <span><b>±</b> ссылка, время и автоматическая уверенность</span>
      </article>
      <section class="section">
        <article class="card">${rows.map(rankRow).join('')||'<div class="empty">Рейтинг сформируется после первых административных решений.</div>'}</article>
      </section>`;
  };

  renderProfile=function(){
    const user=currentUser();
    const name=user?[user.first_name,user.last_name].filter(Boolean).join(' '):'Пользователь';
    const photo=safeUrl(user?.photo_url)||'icon.svg';
    const marks=activeWheels().filter(isJoined);
    const total=Number(app.data.registry?.summary?.total_sources||registryRows().length);
    $('#page-profile').innerHTML=`
      <h1 class="page-title">Профиль</h1>
      <p class="page-subtitle">Личные отметки и уведомления</p>
      <article class="card profile-head">
        <img src="${esc(photo)}" alt=""><div class="profile-copy"><strong>${esc(name||'Пользователь')}</strong><span>${user?.username?`@${esc(user.username)}`:'Telegram Mini App'}</span></div>
        <div class="profile-stats"><div class="profile-stat"><strong>${app.joined.size}</strong><span>Отметок</span></div><div class="profile-stat"><strong>${marks.length}</strong><span>Активных</span></div><div class="profile-stat"><strong>${total}</strong><span>Источников</span></div></div>
      </article>
      <section class="section"><div class="section-head"><h2 class="section-title">Мои отметки</h2><span class="count-pill">${marks.length}</span></div><div>${marks.map(wheelCard).join('')||'<div class="empty">Вы пока не отметили участие в подтверждённых колёсах.</div>'}</div></section>
      <section class="section"><h2 class="section-title">Настройки</h2><article class="card">
        <div class="setting"><div class="setting-copy"><strong>Уведомления</strong><small>Обычные уведомления о подтверждённых колёсах</small></div><button class="switch ${userNotifications?'on':''}" type="button" data-action="toggle-notifications" aria-label="Уведомления"></button></div>
        <div class="setting"><div class="setting-copy"><strong>Автообновление</strong><small>Обновлять данные автоматически</small></div><button class="switch ${app.settings.autoRefresh?'on':''}" data-setting="autoRefresh"></button></div>
        <div class="setting"><div class="setting-copy"><strong>Тактильный отклик</strong><small>Вибрация при действиях</small></div><button class="switch ${app.settings.haptics?'on':''}" data-setting="haptics"></button></div>
        <div class="setting"><div class="setting-copy"><strong>Версия приложения</strong><small>${BRAND}</small></div><span class="row-value">6.0.0</span></div>
      </article></section>`;
  };

  showSourceInfo=function(source){
    const registry=registryFor(source);
    const rating=reputationFor(source);
    const rank=ratingRows().find(row=>String(row.source||'').toLowerCase()===String(source).toLowerCase());
    const history=(rating.events||[]).slice(0,20);
    showDialog(`
      <h2>@${esc(source)}</h2>
      <p class="source-detail-status">${statusMark(registry.status)} ${esc(registry.status_label||'Нет данных')}</p>
      <article class="card source-reason"><strong>Почему так</strong><p>${esc(registry.reason||'Результат проверки ещё не записан.')}</p></article>
      <div class="detail-grid">
        <article><span>Место</span><strong>${rank?.place||'—'}</strong></article>
        <article><span>Оценка</span><strong>${Number(rating.score||0)>0?'+':''}${num(rating.score||0)}</strong></article>
        <article><span>Подтверждено</span><strong>${num(rating.confirmed_wheels||0)}</strong></article>
        <article><span>Неактивных</span><strong>${num(rating.inactive_wheels||0)}</strong></article>
        <article><span>Успешность</span><strong>${Number(rating.success_rate||0)}%</strong></article>
        <article><span>Динамика</span><strong>${Number(rating.trend||0)>0?'+':''}${num(rating.trend||0)}</strong></article>
      </div>
      <h3>История начислений и списаний</h3>
      <div class="history-list">${history.map(event=>`<article><b class="${Number(event.delta||0)>=0?'positive':'negative'}">${Number(event.delta||0)>0?'+':''}${num(event.delta||0)}</b><span>${esc(event.reason||event.signal||'Событие рейтинга')}</span><small>${date(event.at)?.toLocaleString('ru-RU')||''}</small></article>`).join('')||'<div class="empty">История ещё не сформирована.</div>'}</div>
      <div class="actions"><button class="button primary" data-action="open-url" data-url="https://t.me/${esc(source)}">Открыть Telegram</button><button class="button secondary" data-action="close-dialog">Закрыть</button></div>`);
  };

  renderAll=function(){
    renderHome();
    renderStats();
    renderSources();
    renderRating();
    renderProfile();
    updateTimers();
  };

  route=function(name){
    if(!['home','stats','sources','rating','profile'].includes(name))name='home';
    app.route=name;
    $$('.page').forEach(page=>page.classList.toggle('active',page.dataset.page===name));
    $$('.nav-item').forEach(item=>item.classList.toggle('active',item.dataset.route===name));
    $('#headerSubtitle').textContent={
      home:'Актуальные колёса',
      stats:'Статистика',
      sources:'Источники',
      rating:'Рейтинг источников',
      profile:'Профиль'
    }[name];
    window.scrollTo({top:0,behavior:'smooth'});
  };

  function setNotifications(enabled){
    userNotifications=enabled;
    store.set('notificationsEnabled',enabled);
    renderProfile();
    const bot=botUsername();
    if(!bot){
      toast('Откройте настройки уведомлений в боте');
      return;
    }
    const payload=enabled?'notifications_on':'notifications_off';
    const link=`https://t.me/${encodeURIComponent(bot)}?start=${payload}`;
    toast(enabled?'Включаю уведомления в боте':'Отключаю уведомления в боте');
    try{tg?.openTelegramLink?.(link)}catch{}
    if(!tg)window.open(link,'_blank','noopener');
  }

  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-action="toggle-notifications"]');
    if(!button)return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setNotifications(!userNotifications);
  },true);

  store.get('notificationsEnabled',true).then(value=>{
    userNotifications=value!==false;
    if(app.lastSync)renderProfile();
  });

  if(app.lastSync)renderAll();
})();
