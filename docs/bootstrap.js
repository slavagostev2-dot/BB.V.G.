async function initMiniApp() {
  setupTelegram();
  renderHeader();
  bindEvents();
  pullRefresh();
  await loadUser();
  await loadData(true);

  setInterval(updateTimers, 1000);
  setInterval(() => {
    if (app.settings.autoRefresh && document.visibilityState === 'visible') loadData(true);
  }, 60000);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && app.settings.autoRefresh) loadData(true);
  });

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('./service-worker.js').catch(error => console.warn('Service worker:', error));
  }
}

initMiniApp();
