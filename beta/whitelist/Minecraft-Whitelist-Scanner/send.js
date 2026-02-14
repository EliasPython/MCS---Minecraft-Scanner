const net = require('net');
const varint = require('varint');

module.exports = (ip, port, packet, timeout = 5000) => {
    return new Promise(async (resolve, reject) => {
        var data = Buffer.alloc(0);
        var length;

        let timeoutHandler = setTimeout(() => {
            client.destroy();
            resolve('timeout');
        }, timeout);

        const client = new net.Socket();
        client.connect(port, ip, () => client.write(packet));
        client.on('error', err => {
            clearTimeout(timeoutHandler);
            client.destroy();
            resolve(err.toString());
        });
        client.on('data', newData => {
            try {
                if (data.length == 0) {
                    length = varint.decode(newData);
                    newData = newData.subarray(varint.decode.bytes);
                }
                data = Buffer.concat([data, newData]);
                if (data.length >= length) {
                    data = data.subarray(0, length);
                    clearTimeout(timeoutHandler);
                    client.destroy();
                    resolve(data);
                }
            } catch (err) {
                client.destroy();
                resolve(err.toString());
            }
        })
    })
}