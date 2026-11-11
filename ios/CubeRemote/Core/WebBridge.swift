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
              if(v !== "midi") return false;
              S.turnMethod = "midi";
              post({cmd:"setTurnMethod", value:"midi"});
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
              post({cmd:"requestAddress", value:String(u)});
              return "restart";
            },
            switchToAppPort: function(){ return true; },
            midiNextNote: function(){ return S.nextNote || 36; },
            midiPrevNote: function(){ return S.prevNote || 37; },
            setMidiNotes: function(next, prev){
              next = parseInt(next,10); prev = parseInt(prev,10);
              if(!(next>=0&&next<=127) || !(prev>=0&&prev<=127)) return false;
              S.nextNote = next; S.prevNote = prev;
              post({cmd:"setMidiNotes", value:[next, prev]});
              return true;
            }
          };
        })();
        """
    }
}
