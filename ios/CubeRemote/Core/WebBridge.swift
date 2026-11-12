import Foundation
import CubeRemoteCore

/// 页面里 window.CubeApp 的 iOS 实现：同步 getter 读注入的状态缓存，
/// setter 乐观更新并 postMessage 给原生（与 Android JS Bridge 同名同义）。
enum WebBridge {
    static func userScript(state: BridgeState) -> String {
        """
        window.__CUBE__ = \(state.jsonString);
        (function(){
          var S = window.__CUBE__ || {};
          function post(o){
            try { window.webkit.messageHandlers.cube.postMessage(o); } catch(e) {}
          }
          window.__cubeApply = function(next){
            if(!next) return;
            for(var k in next){ if(Object.prototype.hasOwnProperty.call(next,k)) S[k]=next[k]; }
            window.__CUBE__ = S;
          };
          window.CubeApp = {
            platform: function(){ return "ios"; },
            version: function(){ return S.version || "0.1.0"; },
            accEnabled: function(){ return true; },
            usageAccess: function(){ return true; },
            turnTarget: function(){ return ""; },
            listApps: function(){ return "[]"; },
            setTurnTarget: function(){ return false; },
            clearTurnTarget: function(){ return true; },
            openUsageAccess: function(){},
            openAccSettings: function(){ post({cmd:"openSettings"}); },
            turnMethod: function(){ return S.turnMethod || "midi"; },
            setTurnMethod: function(v){
              // iOS 翻谱走 MIDI 唯一路（无障碍手势/媒体键不存在）：仅接受
              // midi，其余回 false 让页面走「设置失败」分支——共享页面模板
              // 会真调用（复审攻击轮：删函数=按钮 TypeError 静默死亡）
              if(v !== "midi") return false;
              S.turnMethod = "midi";
              return true;
            },
            pedalForward: function(){ return S.pedalForward === true; },
            setPedalForward: function(on){
              on = !!on; S.pedalForward = on;
              post({cmd:"setPedalForward", value:on});
              return true;
            },
            turnPort: function(){ return S.turnPort || 0; },
            setTurnPort: function(p){
              p = parseInt(p, 10);
              if(!(p >= 1024 && p <= 65535)) return false;
              S.turnPort = p;
              post({cmd:"setTurnPort", value:p});
              return true;
            },
            requestAddressChange: function(u){
              if(!u) return "invalid";
              var s = String(u).trim();
              if(!s) return "invalid";
              // 轻校验（与 ConnectionInfo.parse 同规）：host:port / http(s)
              // URL / cube://connect?host=…——非法直接回 invalid，不再乐观
              // 回 restart 造成「已保存+格式不对」双重矛盾反馈
              if(/^cube:\\/\\//i.test(s)){
                var qp = new URLSearchParams(s.split("?")[1] || "");
                if(!(qp.get("host") || qp.get("ip"))) return "invalid";
                post({cmd:"requestAddress", value:s});
                return "restart";
              }
              var url = s;
              if(!/^[a-z][a-z0-9+.-]*:\\/\\//i.test(url)) url = "http://" + url;
              if(!/^https?:\\/\\//i.test(url)) return "invalid";
              var probe = document.createElement("a");
              probe.href = url;
              var port = probe.port ? parseInt(probe.port, 10) : 8767;
              if(!probe.hostname || !(port >= 1 && port <= 65535)) return "invalid";
              post({cmd:"requestAddress", value:s});
              return "restart";
            },
            switchToAppPort: function(){ return true; }
          };
        })();
        """
    }
}
