// CubeSetlistRemote controller script for Studio One (script device).
// Dumb protocol: 1 CC = 1 command -- the surface XML does all the binding.
// SEQ semantics live in Python (mr_ctrl.SEQ); CC table matches the Cubase
// driver verbatim so one protocol serves both DAWs on the same port.
// CC-TABLE: start=102 stop=103 rtz=104 save=105 close=106
include_file("resource://com.presonus.musicdevices/sdk/midiprotocol.js");
include_file("resource://com.presonus.musicdevices/sdk/controlsurfacedevice.js");

class CubeSetlistRemoteDevice extends PreSonus.ControlSurfaceDevice {
    constructor() {
        super();
        this.debugLog = false;
    }
    onInit(hostDevice) {
        super.onInit(hostDevice);
        this.debugLog = false;
    }
}

function createCubeSetlistRemoteInstance() {
    return new CubeSetlistRemoteDevice();
}
