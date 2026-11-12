import XCTest
@testable import CubeRemoteCore

final class NoteConventionTests: XCTestCase {
    func testNoteNamesMatchDesktopConvention() {
        // 与 PC 端 kbd_auto.note_name 同锚：60=C3、72=C4（Cubase 显示口径）。
        // 两端各自实现，改口径必须同步 kbd_auto.py 与此处。
        XCTAssertEqual(NoteNames.name(60), "C3")
        XCTAssertEqual(NoteNames.name(72), "C4")
        XCTAssertEqual(NoteNames.name(48), "C2")
        XCTAssertEqual(NoteNames.name(49), "C#2")
        XCTAssertEqual(NoteNames.name(84), "C5")
        XCTAssertEqual(NoteNames.name(127), "G8")
    }

    func testTurnNoteDefaultsAnchor() {
        // 与 web_remote.CMD_NEXT/CMD_PREV 同号：48/49
        XCTAssertEqual(TurnNotes.next, 48)
        XCTAssertEqual(TurnNotes.prev, 49)
        XCTAssertEqual(NoteNames.name(Int(TurnNotes.next)), "C2")
        XCTAssertEqual(NoteNames.name(Int(TurnNotes.prev)), "C#2")
    }
}

final class HTTPParserTests: XCTestCase {
    func testPostWithBody() throws {
        let raw = "POST /turn HTTP/1.1\r\nHost: 192.168.1.5:8766\r\n"
            + "Content-Type: application/json\r\nContent-Length: 14\r\n\r\n"
            + "{\"dir\":\"next\"}"
        let data = Data(raw.utf8)
        let req = try XCTUnwrap(HTTPParser.parse(data))
        XCTAssertEqual(req.method, "POST")
        XCTAssertEqual(req.path, "/turn")
        XCTAssertEqual(req.headers["host"], "192.168.1.5:8766")
        XCTAssertEqual(String(data: req.body, encoding: .utf8), "{\"dir\":\"next\"}")
    }

    func testIncompleteReturnsNil() throws {
        let raw = "POST /turn HTTP/1.1\r\nContent-Length: 14\r\n\r\n{\"di"
        XCTAssertNil(try HTTPParser.parse(Data(raw.utf8)))
    }

    func testMalformedThrows() {
        XCTAssertThrowsError(try HTTPParser.parse(Data("GET\r\n\r\n".utf8)))
        XCTAssertThrowsError(try HTTPParser.parse(
            Data("GET / HTTP/1.1\r\nBadHeader\r\n\r\n".utf8)))
    }

    func testQueryDecoding() throws {
        let raw = "GET /state?t=a%20b&x=1 HTTP/1.1\r\nHost: h\r\n\r\n"
        let req = try XCTUnwrap(HTTPParser.parse(Data(raw.utf8)))
        XCTAssertEqual(req.path, "/state")
        XCTAssertEqual(req.query["t"], "a b")
        XCTAssertEqual(req.query["x"], "1")
    }

    func testBodyTooLarge() {
        let raw = "POST / HTTP/1.1\r\nContent-Length: 2000000\r\n\r\n"
        XCTAssertThrowsError(try HTTPParser.parse(Data(raw.utf8))) { err in
            XCTAssertEqual(err as? HTTPParseError, .bodyTooLarge)
        }
    }

    func testResponseIsWellFormed() throws {
        let data = HTTPResponseWriter.json(status: 200, object: ["ok": true])
        let text = try XCTUnwrap(String(data: data, encoding: .utf8))
        XCTAssertTrue(text.hasPrefix("HTTP/1.1 200 OK\r\n"))
        XCTAssertTrue(text.contains("Content-Length: "))
        XCTAssertTrue(text.hasSuffix("{\"ok\":true}"))
    }
}

final class TurnProtocolTests: XCTestCase {
    func testDecode() throws {
        let next = try XCTUnwrap(TurnRequest(body: Data("{\"dir\":\"next\"}".utf8)))
        XCTAssertEqual(next.direction, .next)
        let prev = try XCTUnwrap(TurnRequest(
            body: Data("{\"dir\":\"prev\",\"test\":1}".utf8)))
        XCTAssertEqual(prev.direction, .prev)   // test 旗标 iOS 不解析，按正常翻页演示
    }

    func testRejectsUnknownDirection() {
        XCTAssertNil(TurnRequest(body: Data("{\"dir\":\"up\"}".utf8)))
        XCTAssertNil(TurnRequest(body: Data("not json".utf8)))
    }

    func testTokenField() throws {
        let t = try XCTUnwrap(TurnRequest(
            body: Data("{\"dir\":\"next\",\"t\":\"s3cret\"}".utf8)))
        XCTAssertEqual(t.token, "s3cret")
        let bare = try XCTUnwrap(TurnRequest(
            body: Data("{\"dir\":\"next\"}".utf8)))
        XCTAssertEqual(bare.token, "")
    }
}

final class PedalProtocolTests: XCTestCase {
    func testKeyMappingFitsF13ToF24() {
        for note in 0..<128 {
            let vk = PedalKeyMap.lkc(forNote: UInt8(note))
            XCTAssertTrue((183...194).contains(vk))
        }
        XCTAssertEqual(PedalKeyMap.lkc(forNote: 36), 183)
        XCTAssertEqual(PedalKeyMap.lkc(forNote: 37), 184)
    }

    func testEventPacketShape() throws {
        let p = PedalPacket(device: "iOS", seq: 7, et: 1234, sid: "abcd1234",
                            events: [PedalEvent(vk: 183, down: true)])
        let obj = p.jsonObject
        XCTAssertEqual(obj["device"] as? String, "iOS")
        XCTAssertEqual(obj["seq"] as? Int, 7)
        XCTAssertNil(obj["hb"])
        let events = try XCTUnwrap(obj["events"] as? [[String: Any]])
        XCTAssertEqual(events.first?["vk"] as? Int, 183)
        XCTAssertEqual(events.first?["kc"] as? Int, 0)
        XCTAssertEqual(events.first?["down"] as? Bool, true)
    }

    func testHeartbeatHasNoEvents() {
        let p = PedalPacket(device: "iOS", seq: 8, et: 99, sid: "s",
                            heartbeat: true)
        XCTAssertEqual(p.jsonObject["hb"] as? Bool, true)
        XCTAssertNil(p.jsonObject["events"])
    }
}

final class ConnectionInfoTests: XCTestCase {
    func testBareHostDefaultsToAppPort() {
        let c = ConnectionInfo.parse("192.168.1.5")
        XCTAssertEqual(c?.host, "192.168.1.5")
        XCTAssertEqual(c?.port, 8767)
    }

    func testHostPort() {
        let c = ConnectionInfo.parse("192.168.1.5:9000")
        XCTAssertEqual(c?.port, 9000)
    }

    func testURLWithToken() {
        let c = ConnectionInfo.parse("http://192.168.1.5:8767/?t=abc")
        XCTAssertEqual(c?.token, "abc")
        XCTAssertEqual(c?.pageURL?.query, "t=abc")
    }

    func testCubeScheme() {
        let c = ConnectionInfo.parse(
            "cube://connect?host=10.0.0.2&port=8766&token=xyz")
        XCTAssertEqual(c?.host, "10.0.0.2")
        XCTAssertEqual(c?.port, 8766)
        XCTAssertEqual(c?.token, "xyz")
    }

    func testRejectsEmptyOrBadPort() {
        XCTAssertNil(ConnectionInfo.parse("  "))
        XCTAssertNil(ConnectionInfo.parse("http://h:99999"))
    }

    func testDuplicateQueryKeysFirstWins() {
        // 恶意/损坏二维码带重复键：不得 trap，首个生效
        let c = ConnectionInfo.parse(
            "cube://connect?host=10.0.0.1&host=10.0.0.2&port=9000&port=1")
        XCTAssertEqual(c?.host, "10.0.0.1")
        XCTAssertEqual(c?.port, 9000)
    }
}

final class BridgeStateTests: XCTestCase {
    func testJSONRoundTrip() throws {
        var s = BridgeState(version: "0.1.0", turnPort: 8766, listening: true)
        s.pedalForward = true
        let data = try XCTUnwrap(s.jsonString.data(using: .utf8))
        let obj = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(obj["platform"] as? String, "ios")
        XCTAssertEqual(obj["turnPort"] as? Int, 8766)
        XCTAssertEqual(obj["pedalForward"] as? Bool, true)
        XCTAssertEqual(obj["listening"] as? Bool, true)
    }
}
