'use strict';

const VERSION='2.0.0';
const REPO='slavagostev2-dot/betboom-wheel-monitor';
const ORIGINS=[`https://raw.githubusercontent.com/${REPO}/main/`,`https://cdn.jsdelivr.net/gh/${REPO}@main/`];
const tg=window.Telegram?.WebApp||null;
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const app={route:'home',filter:'all',days:7,sourceMode:'primary',query:'',limit:80,loading:false,lastSync:null,data:{state:{},stats:{daily:{},sources:{}},health:{sources:{}},primary:[],nightly:[],monitor:{}},joined:new Set(),favorites:new Set(),settings:{autoRefresh:true,haptics:true}};

const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
const date=v=>{if(!v)return null;const d=new Date(v);return Number.isNaN(+d)?null:d};
const fmt=(v,timeOnly=false)=>{const d=v instanceof Date?v:date(v);return d?d.toLocaleString('ru-RU',timeOnly?{hour:'2-digit',minute:'2-digit'}:{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'нет данных'};
const num=v=>new Intl.NumberFormat('ru-RU').format(Number(v||0));
const compact=v=>new Intl.NumberFormat('ru-RU',{notation:'compact',maximumFractionDigits:1}).format(Number(v||0));
const safeUrl=v=>{try{const u=new URL(String(v||''));return /^https?:$/.test(u.protocol)?u.toString():''}catch{return''}};
const age=v=>{const d=v instanceof Date?v:date(v);if(!d)return{minutes:Infinity,text:'нет данных'};const s=Math.max(0,(Date.now()-d)/1000);return s<60?{minutes:0,text:'только что'}:s<3600?{minutes:s/60,text:`${Math.floor(s/60)} мин. назад`}:s<86400?{minutes:s/60,text:`${Math.floor(s/3600)} ч. назад`}:{minutes:s/60,text:`${Math.floor(s/86400)} дн. назад`}};
const left=v=>{const d=v instanceof Date?v:date(v);if(!d)return'время не определено';const s=Math.floor((+d-Date.now())/1000);if(s<=0)return'время наступило';const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;return h?`${h} ч. ${m} мин.`:m?`${m} мин. ${String(x).padStart(2,'0')} сек.`:`${x} сек.`};
const initials=v=>String(v||'BetBoom').trim().split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase()||'BB';
const parseList=t=>[...new Map(String(t||'').split(/\r?\n/).map(x=>x.split('#')[0].trim().replace(/^@/,'')).filter(Boolean).map(x=>[x.toLowerCase(),x])).values()];

let toastTimer;
function toast(text){const el=$('#toast');el.textContent=text;el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),2200)}
function haptic(type='light'){if(!app.settings.haptics||!tg?.HapticFeedback)return;try{['success','warning','error'].includes(type)?tg.HapticFeedback.notificationOccurred(type):tg.HapticFeedback.impactOccurred(type)}catch{}}
function user(){return tg?.initDataUnsafe?.user||null}

function setupTelegram(){if(!tg)return;try{tg.ready();tg.expand();tg.setHeaderColor?.('secondary_bg_color');tg.setBackgroundColor?.('bg_color');tg.setBottomBarColor?.('bottom_bar_bg_color');tg.disableVerticalSwipes?.()}catch(e){console.warn(e)}}
function renderHeader(){const u=user(),name=u?[u.first_name,u.last_name].filter(Boolean).join(' '):'BetBoom Monitor';$('#userName').textContent=name||'BetBoom Monitor';const a=$('#userAvatar');a.innerHTML=u?.photo_url?`<img src="${esc(u.photo_url)}" alt="" referrerpolicy="no-referrer">`:esc(initials(name))}

const store={
 get(key,fallback){return new Promise(resolve=>{if(tg?.CloudStorage?.getItem)tg.CloudStorage.getItem(key,(e,v)=>{if(!e&&v){try{return resolve(JSON.parse(v))}catch{}}resolve(this.localGet(key,fallback))});else resolve(this.localGet(key,fallback))})},
 set(key,value){const raw=JSON.stringify(value);try{localStorage.setItem(`bb:${key}`,raw)}catch{};if(tg?.CloudStorage?.setItem)tg.CloudStorage.setItem(key,raw,()=>{})},
 localGet(key,fallback){try{const v=localStorage.getItem(`bb:${key}`);return v?JSON.parse(v):fallback}catch{return fallback}}
};

async function loadUser(){const[j,f,s]=await Promise.all([store.get('joined',[]),store.get('favorites',[]),store.get('settings',app.settings)]);app.joined=new Set(Array.isArray(j)?j.map(x=>String(x).toLowerCase()):[]);app.favorites=new Set(Array.isArray(f)?f.map(x=>String(x).toLowerCase()):[]);app.settings={autoRefresh:s?.autoRefresh!==false,haptics:s?.haptics!==false}}
