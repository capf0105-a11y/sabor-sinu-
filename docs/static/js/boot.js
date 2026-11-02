/* Arranque temprano: tema, aviso de espera y botón de reintento.
   Se carga antes de la hoja de estilos para evitar destellos. */
(function () {
  try {
    var saved = localStorage.getItem('sinu_theme');
    var theme = saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    if (theme === 'dark') document.documentElement.classList.add('dark');
  } catch (error) {
    /* sin almacenamiento: se usa el tema del sistema */
  }

  function revealRetry() {
    var button = document.getElementById('appSplashRetry');
    if (button) button.hidden = false;
  }

  function wireRetry() {
    var button = document.getElementById('appSplashRetry');
    if (!button) return;
    button.addEventListener('click', function () {
      window.location.reload();
    });
    window.setTimeout(revealRetry, 18000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireRetry);
  } else {
    wireRetry();
  }
})();
