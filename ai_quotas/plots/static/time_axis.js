const WD=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
const MO=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function startOfLocalDay(sec){
  const d=new Date(sec*1000); d.setHours(0,0,0,0); return d;
}
function startOfLocalMonday(sec){
  const d=startOfLocalDay(sec);
  d.setDate(d.getDate()-((d.getDay()+6)%7));
  return d;
}
function startOfLocalMonth(sec){
  const d=startOfLocalDay(sec); d.setDate(1); return d;
}
function stepHours(origin, hours, minSec, maxSec, limit){
  const out=[]; const d=new Date(origin.getTime()); let n=0;
  while (d.getTime()/1000<=maxSec && n++<limit){
    const t=Math.floor(d.getTime()/1000);
    if (t>=minSec) out.push(t);
    d.setHours(d.getHours()+hours);
  }
  return out;
}
function stepDays(origin, nDays, minSec, maxSec, limit){
  const out=[]; const d=new Date(origin.getTime()); let n=0;
  const span=nDays*86400;
  while (d.getTime()/1000<=maxSec && n++<limit){
    const t=Math.floor(d.getTime()/1000);
    // keep the bucket that overlaps the view, even if midnight is a few hours before min
    if (t<=maxSec && (t+span)>minSec) out.push(t);
    d.setDate(d.getDate()+nDays);
  }
  return out;
}
function fmtHM(t){
  const d=new Date(t*1000);
  return `${WD[d.getDay()]} ${String(d.getHours()).padStart(2,'0')}:00`;
}
function fmtDay(t){
  const d=new Date(t*1000);
  return `${WD[d.getDay()]} ${String(d.getDate()).padStart(2,'0')} ${MO[d.getMonth()]}`;
}
function fmtShortDay(t){
  const d=new Date(t*1000);
  return `${WD[d.getDay()]} ${String(d.getDate()).padStart(2,'0')}`;
}
function fmtDate(t){
  const d=new Date(t*1000);
  return `${String(d.getDate()).padStart(2,'0')} ${MO[d.getMonth()]}`;
}
function fmtMonth(t){
  const d=new Date(t*1000);
  return `${MO[d.getMonth()]} ${d.getFullYear()}`;
}
function timeAxis(minSec, maxSec, widthPx){
  if (!isFinite(minSec) || !isFinite(maxSec) || maxSec<=minSec){
    return {majors:[], minors:[], fmt:()=>''};
  }
  const days=(maxSec-minSec)/86400;
  const budget=Math.max(4, Math.min(12, Math.floor((widthPx||720)/88)));
  const day0=startOfLocalDay(minSec);
  const mon0=startOfLocalMonday(minSec);
  const daily=stepDays(day0, 1, minSec, maxSec, 400);
  const every2=stepDays(day0, 2, minSec, maxSec, 80);
  const weekly=stepDays(mon0, 7, minSec, maxSec, 40);
  const biweekly=stepDays(mon0, 14, minSec, maxSec, 40);
  let majors, minors, fmt;
  if (days<=2.2){
    majors=stepHours(day0, 6, minSec, maxSec, 80);
    minors=stepHours(day0, 1, minSec, maxSec, 400);
    fmt=fmtHM;
  } else if (daily.length<=budget){
    majors=daily;
    minors=stepHours(day0, 6, minSec, maxSec, 400);
    fmt=fmtDay;
  } else if (every2.length<=budget){
    majors=every2;
    minors=daily;
    fmt=fmtShortDay;
  } else if (weekly.length<=budget){
    majors=weekly;
    minors=daily;
    fmt=fmtDate;
  } else if (biweekly.length<=budget){
    majors=biweekly;
    minors=weekly;
    fmt=fmtDate;
  } else {
    majors=[];
    const d=startOfLocalMonth(minSec); let n=0;
    while (d.getTime()/1000<=maxSec && n++<48){
      const t=Math.floor(d.getTime()/1000);
      if (t+31*86400>minSec && t<=maxSec) majors.push(t);
      d.setMonth(d.getMonth()+1);
    }
    minors=weekly;
    fmt=fmtMonth;
  }
  const majorSet=new Set(majors);
  minors=minors.filter(t=>!majorSet.has(t));
  return {majors, minors, fmt};
}
