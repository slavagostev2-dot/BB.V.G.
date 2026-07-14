'use strict';
(()=>{
  const KEY='bbvg:appearance';
  const root=document.documentElement;
  const meta=document.querySelector('meta[name="theme-color"]');
  const tg=window.Telegram?.WebApp||null;
  const read=()=>{try{return localStorage.getItem(KEY)==='light'?'light':'dark'}catch{return'dark'}};
  function updateToggle(){
    const button=document.querySelector('[data-theme-toggle]');
    if(!button)return;
    const light=root.dataset.theme==='light';
    button.classList.toggle('on',light);
    button.setAttribute('aria-pressed',String(light));
    button.setAttribute('aria-label',light?'Отключить светлую тему':'Включить светлую тему');
  }
  function apply(theme,persist=true){
    const value=theme==='light'?'light':'dark';
    root.dataset.theme=value;
    root.style.colorScheme=value;
    meta?.setAttribute('content',value==='light'?'#f4f1f8':'#08080c');
    try{
      tg?.setHeaderColor?.(value==='light'?'#f8f5fb':'#08080c');
      tg?.setBackgroundColor?.(value==='light'?'#f4f1f8':'#08080c');
      tg?.setBottomBarColor?.(value==='light'?'#faf8fc':'#0c0b11');
    }catch{}
    if(persist)try{localStorage.setItem(KEY,value)}catch{}
    updateToggle();
  }
  function inject(){
    const anchor=document.querySelector('#page-profile .setting [data-setting="haptics"]')?.closest('.setting');
    if(!anchor||document.querySelector('[data-theme-setting]'))return;
    const row=document.createElement('div');
    row.className='setting';
    row.dataset.themeSetting='true';
    row.innerHTML='<div class="setting-copy"><strong>Светлая тема</strong><small>Светлый фон и тёмный текст</small></div><button class="switch" type="button" data-theme-toggle aria-pressed="false"></button>';
    anchor.insertAdjacentElement('afterend',row);
    updateToggle();
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-theme-toggle]');
    if(!button)return;
    event.preventDefault();
    event.stopPropagation();
    apply(root.dataset.theme==='light'?'dark':'light');
    try{tg?.HapticFeedback?.impactOccurred?.('light')}catch{}
  },true);
  apply(read(),false);
  new MutationObserver(inject).observe(document.documentElement,{childList:true,subtree:true});
  document.addEventListener('DOMContentLoaded',inject);
  inject();
})();
