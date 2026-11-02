// Phone or desktop layout, decided in <head> before the first layout.
// Petr, 3 Nov 2026: "the 'request desktop version' doesn't work (probably
// because of dimension-based layout..)". Order: the header toggle's saved
// choice, then the browser's desktop-site request, then the width. Phone CSS
// is scoped to html[data-view=phone], never to a width media query.
const QUOTA_VIEW_KEY = 'quota-view';
const QUOTA_PHONE_MAX = 430;
const QUOTA_DESKTOP_WIDTH = 1100;

// iOS Safari "Request Desktop Website" sends a Mac user agent from a touch
// screen (iPads do this by default; they are wide enough for desktop anyway).
// Android Chrome's "Desktop site" sends a Linux desktop agent.
function quotaDesktopRequest(env) {
  if ((Number(env.touch) || 0) < 2) return false;
  const ua = env.ua || '';
  if (/Mobi|Android|iPhone|iPod/i.test(ua)) return false;
  return env.platform === 'MacIntel' || /\bX11; Linux x86_64\b/.test(ua);
}
function quotaAutoView(env) {
  if (quotaDesktopRequest(env)) return 'desktop';
  return env.width > 0 && env.width <= QUOTA_PHONE_MAX ? 'phone' : 'desktop';
}
// What the device picks with no saved choice. A desktop width this page forced
// is not the device's: the screen width stands in for it.
function quotaUnsavedView(env, mode) {
  const forced = !quotaViewport(mode, env).startsWith('width=device-width');
  return quotaAutoView({...env, saved: null, width: forced ? env.screen : env.width});
}
function quotaViewMode(env) {
  return env.saved === 'phone' || env.saved === 'desktop' ? env.saved : quotaAutoView(env);
}
// A phone screen asked for desktop lays out at a desktop width, zoomed out
// to fit (a reload would otherwise keep the phone zoom); everything else keeps
// the device width (a desktop browser ignores the tag).
function quotaViewport(mode, env) {
  const asked = env.saved === 'desktop' || (!env.saved && quotaDesktopRequest(env));
  const phoneScreen = env.screen > 0 && env.screen <= 600;
  if (mode !== 'desktop' || !asked || !phoneScreen) return 'width=device-width, initial-scale=1';
  const scale = Math.round(env.screen / QUOTA_DESKTOP_WIDTH * 1000) / 1000;
  return `width=${QUOTA_DESKTOP_WIDTH}, initial-scale=${scale}`;
}
// The toggle shows only where a phone layout is plausible.
function quotaViewSwitchable(env) {
  return !!env.saved || quotaDesktopRequest(env)
    || (env.screen > 0 && env.screen <= 600)
    || (env.width > 0 && env.width <= QUOTA_PHONE_MAX);
}
function quotaViewEnv(win) {
  let saved = null;
  try { saved = win.localStorage.getItem(QUOTA_VIEW_KEY); } catch (_) { /* private mode */ }
  const nav = win.navigator || {};
  const scr = win.screen || {};
  const sizes = [scr.width, scr.height].filter(n => n > 0);
  return {
    saved: saved === 'phone' || saved === 'desktop' ? saved : null,
    platform: nav.platform || '',
    ua: nav.userAgent || '',
    touch: nav.maxTouchPoints || 0,
    width: win.innerWidth || 0,
    screen: sizes.length ? Math.min(...sizes) : 0,
  };
}
function quotaApplyView(win) {
  const env = quotaViewEnv(win);
  const mode = quotaViewMode(env);
  const doc = win.document;
  doc.documentElement.dataset.view = mode;
  const meta = doc.querySelector('meta[name="viewport"]');
  const content = quotaViewport(mode, env);
  if (meta && meta.getAttribute('content') !== content) meta.setAttribute('content', content);
  return mode;
}
function quotaPhoneView() {
  return typeof document !== 'undefined' && document.documentElement.dataset.view === 'phone';
}
const QUOTA_VIEW_ICONS = {
  desktop: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/></svg>',
  phone: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="7" y="3" width="10" height="18" rx="2"/><path d="M11 18h2"/></svg>',
};
function quotaBindViewToggle(win) {
  const doc = win.document;
  const env = quotaViewEnv(win);
  const mode = doc.documentElement.dataset.view || quotaViewMode(env);
  const next = mode === 'phone' ? 'desktop' : 'phone';
  const label = next === 'desktop' ? 'Desktop layout' : 'Phone layout';
  doc.querySelectorAll('[data-view-toggle]').forEach(btn => {
    btn.hidden = !quotaViewSwitchable(env);
    btn.innerHTML = QUOTA_VIEW_ICONS[next];
    btn.title = label;
    btn.setAttribute('aria-label', label);
    btn.onclick = () => {
      try {
        // Choosing what the device would pick anyway forgets the choice, so a
        // later desktop-site request works again.
        if (next === quotaUnsavedView(env, mode)) win.localStorage.removeItem(QUOTA_VIEW_KEY);
        else win.localStorage.setItem(QUOTA_VIEW_KEY, next);
      } catch (_) { /* not saved: the reload below keeps the old layout */ }
      // The live page holds this one in an iframe; its viewport tag rules. A
      // fresh load, not reload(): a reload keeps the old zoom.
      let loc = win.location;
      try { loc = win.top.location; void loc.pathname; } catch (_) { loc = win.location; }
      loc.replace(loc.pathname + loc.search);
    };
  });
}
if (typeof window !== 'undefined' && window.document) {
  quotaApplyView(window);
  window.addEventListener('resize', () => {
    const env = quotaViewEnv(window);
    if (env.saved || quotaDesktopRequest(env)) return;
    // A width-decided layout follows the window (narrow desktop window, rotation).
    quotaApplyView(window);
    quotaBindViewToggle(window);
  });
  document.addEventListener('DOMContentLoaded', () => quotaBindViewToggle(window));
}
