// live_refresh.js — in-place refresh for the engine pages, no page reload.
// Plain functions and one poller; tests run this file under node with a fake fetch.
//
// Contract with the renderer (dash.py / generate.py):
//   meta.json    {generated_at, poll_interval_s}  rewritten after every regeneration
//   panels.json  {shell_version, panels, ...}      the data the page draws
// The page polls meta.json (tiny) and re-fetches panels.json only when
// generated_at changes. No meta.json (plain `ai-quotas plot` output, 404) means
// one paint and no polling. A different shell_version means the page sources
// were redeployed: one full reload, deferred while a dialog is open.
function pollDelayMs(meta, fallbackMs) {
  var s = meta && Number(meta.poll_interval_s);
  if (!(s > 0)) return fallbackMs;
  return Math.max(5000, Math.round(s * 1000));
}
function metaChanged(prev, meta) {
  return !!(meta && meta.generated_at && meta.generated_at !== prev);
}
function shellOutdated(shellVersion, data) {
  return !!(shellVersion && data && data.shell_version && data.shell_version !== shellVersion);
}
function createLivePoller(opts) {
  // opts: fetchImpl, metaUrl, dataUrl, shellVersion, onData(data, {initial}),
  //       onOutdated(), isBusy() (dialog open), hidden() (tab in background),
  //       fallbackMs, setTimeoutImpl / clearTimeoutImpl (tests)
  var fetchImpl = opts.fetchImpl;
  var setT = opts.setTimeoutImpl || function (fn, ms) { return setTimeout(fn, ms); };
  var clearT = opts.clearTimeoutImpl || function (id) { clearTimeout(id); };
  var seen = null, timer = null, stopped = false, outdated = false;
  var delay = opts.fallbackMs || 15000;

  function getJson(url) {
    return fetchImpl(url, { cache: 'no-cache' }).then(function (r) {
      if (r.status === 404) return { __missing: true };
      if (!r.ok) throw new Error(url + ' ' + r.status);
      return r.json();
    });
  }
  function loadData(initial) {
    return getJson(opts.dataUrl).then(function (d) {
      if (d.__missing) throw new Error(opts.dataUrl + ' missing');
      if (shellOutdated(opts.shellVersion, d)) outdated = true;
      opts.onData(d, { initial: initial });
      return d;
    });
  }
  function readMeta() {
    return getJson(opts.metaUrl).then(function (m) {
      if (m.__missing) { stopped = true; return null; }
      delay = pollDelayMs(m, delay);
      return m;
    });
  }
  function maybeReload() {
    if (!outdated) return false;
    if (opts.isBusy && opts.isBusy()) return false;
    if (opts.onOutdated) opts.onOutdated();
    return true;
  }
  function schedule() {
    if (stopped) return;
    clearT(timer);
    timer = setT(tick, delay);
  }
  function tick() {
    if (opts.hidden && opts.hidden()) { schedule(); return Promise.resolve(false); }
    var changed = false;
    return readMeta().then(function (m) {
      if (!m) return;
      changed = metaChanged(seen, m);
      seen = m.generated_at || seen;
      if (changed) return loadData(false);
    }).catch(function () {}).then(function () {
      maybeReload();
      schedule();
      return changed;
    });
  }
  function start() {
    // meta first: a regeneration landing between the two fetches is then
    // caught on the next tick instead of waiting for the one after
    var meta = readMeta().then(function (m) { if (m) seen = m.generated_at || null; }).catch(function () {});
    var data = loadData(true);
    return Promise.all([meta, data]).then(function (r) { maybeReload(); schedule(); return r[1]; });
  }
  function refresh() {
    return loadData(false).then(function (d) { maybeReload(); return d; });
  }
  function wake() {
    if (stopped) return Promise.resolve(false);
    clearT(timer);
    return tick();
  }
  function stop() { stopped = true; clearT(timer); }
  return { start: start, refresh: refresh, wake: wake, stop: stop,
           _state: function () { return { seen: seen, delay: delay, stopped: stopped, outdated: outdated }; } };
}
