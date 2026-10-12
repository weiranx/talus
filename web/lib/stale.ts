// After a deploy, a tab opened on the previous version still asks for that version's script
// chunks, which are gone, and Next shows "Application error". Reload once to pick up the new
// version. The timestamp guard stops a reload loop if a chunk is missing for some other reason.
export const STALE_RELOAD_SCRIPT = `(function(){
var K="talus-stale-reload";
function reload(){try{var t=+sessionStorage.getItem(K)||0;if(Date.now()-t<30000)return;sessionStorage.setItem(K,String(Date.now()))}catch(e){}location.reload()}
addEventListener("error",function(e){var s=e.target;if(s&&s.tagName==="SCRIPT"&&/\\/_next\\/static\\//.test(s.src))reload()},true);
addEventListener("unhandledrejection",function(e){var r=e.reason;if(r&&(r.name==="ChunkLoadError"||/Loading chunk [^ ]+ failed/.test(r.message||"")))reload()});
})();`;
