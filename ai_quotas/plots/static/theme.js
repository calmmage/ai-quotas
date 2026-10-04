(function () {
  const KEY = "quota-theme";
  const DAY = "../03_plotly/index.html";
  const NIGHT = "../10_uplot/index.html";
  function isNightHere() {
    return location.pathname.indexOf("10_uplot") !== -1;
  }
  function go(t) {
    localStorage.setItem(KEY, t);
    const wantNight = t === "night";
    if (wantNight === isNightHere()) return;
    location.href = wantNight ? NIGHT : DAY;
  }
  document.querySelectorAll("[data-theme]").forEach((btn) => {
    const on = (btn.getAttribute("data-theme") === "night") === isNightHere();
    btn.classList.toggle("active", on);
    btn.addEventListener("click", () => go(btn.getAttribute("data-theme")));
  });
})();
