const varint = require('varint');
const send = require('./send.js');

module.exports = (ip, port, protocol) => new Promise(async (res, rej) => {
    const handshakePacket = Buffer.concat([
        Buffer.from([0x00]), // packet ID
        Buffer.from(varint.encode(protocol)), //protocol version
        Buffer.from([ip.length]),
        Buffer.from(ip, 'utf-8'), // server address
        Buffer.from(new Uint16Array([port]).buffer).reverse(), // server port
        Buffer.from([0x01]), // next state (2)
        Buffer.from([0x01]), // second packet length
        Buffer.from([0x00]) // status request
    ]);
    var packetLength = Buffer.alloc(1);
    packetLength.writeUInt8(handshakePacket.length - 2);
    var buffer = Buffer.concat([packetLength, handshakePacket]);
    var response = await send(ip, port, buffer, 6000);
    if (typeof response == 'string') {
        res(`Error: ${response}`);
        return;
    }
    if (response[0] != 0) {
        res('Error: not a Minecraft server');
        return;
    }
    response = response.subarray(1);
    const fieldLength = varint.decode(response);
    response = response.subarray(varint.decode.bytes, fieldLength + varint.decode.bytes).toString();
    try {
        res(JSON.parse(response));
    } catch (error) {
        //console.log(error, response)
        res('Error');
    }
});