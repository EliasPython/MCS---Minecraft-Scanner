const fs = require('fs');
const path = require('path');
const mineflayer = require('mineflayer');
const auth = require('prismarine-auth');
const mcData = require('minecraft-data');
const protocol = require('minecraft-protocol');
const ping = require('./ping.js');
const config = require('./config.json');
const pg = require('pg');
client = new pg.Client({
    host: config.sql.host,
    port: config.sql.port,
    user: config.sql.user,
    password: config.sql.password,
    database: config.sql.database,
    ssl: {
        require: true,
        rejectUnauthorized: false
    }
});

function join(account, ip, port, version) {
    return new Promise(async (resolve, reject) => {
        await refreshToken(account);
        let endTimeout = setTimeout(() => resolve(null), 6000);

        try {
            const bot = mineflayer.createBot({
                host: ip,
                port,
                version,
                auth: 'microsoft',
                username: account.username,
                profilesFolder: path.join(__dirname, '.auth-cache'),
                logErrors: !config.suppressLogs,
                hideErrors: config.suppressLogs
            })

            bot.on('login', async () => {
                resolve(false);
                clearTimeout(endTimeout);
                if (config.chatWarning) {
                    endTimeout = setTimeout(() => {
                        bot.end();
                        resolve(false);
                    }, 3000);
                    bot.on('chat', (username, message) => {
                        if (bot.username != username) return;
                        clearTimeout(endTimeout);
                        bot.end();
                    });

                    bot.chat('WARNING: If you don\'t want your server to be joined (and likely destroyed) by random people, the only way to protect your server is by enabling a whitelist. Banning this bot will NOT protect your server.');
                    bot.chat('If this is intended to be a public server, simply ban this bot and my messages will stop. DM @cornbread2100 on Discord for more info.');
                } else bot.end();
            });

            // Log errors and kick reasons:
            bot.on('kicked', (reason) => {
                if (typeof reason == 'object') reason = JSON.stringify(reason);
                // console.log(`Kicked from ${ip}:${port}`, reason);
                if (reason.includes('You are not whitelisted on this server') || reason.includes('multiplayer.disconnect.not_whitelisted')) resolve(true);
                else resolve(null);
            });
            bot.on('error', (err) => {
                // console.log(`Error on ${ip}:${port} ${version}`, err);
                if (err.message.includes('RateLimiter disallowed request') || err.message.includes('Failed to obtain profile data')) resolve('retry');
                else resolve(null);
            });
        } catch (err) {
            resolve(null);
        }
    });
}

async function refreshToken(account) {
    await (new auth.Authflow(account.username, path.join(__dirname, '.auth-cache'), {
        flow: 'live',
        password: account.password,
        authTitle: auth.Titles.MinecraftJava,
        deviceType: 'Win32'
    })).getMinecraftJavaToken();
}

async function scan() {
    await client.connect();
    console.log('Connected to database');
    let ips = config.customIps ? fs.readFileSync(config.ipsPath) : Buffer.from(await (await fetch('https://github.com/kgurchiek/Minecraft-Server-Scanner/raw/main/ips')).arrayBuffer());
    let startIndex = Math.floor(Math.random() * ips.length / 6) * 6;
    ips = Buffer.concat([ips.slice(startIndex), ips.slice(0, startIndex)]);
    console.log(`Scanning ${(ips.length / 6).toLocaleString()} servers`);

    async function check(index) {
        const account = config.accounts[(index / 6) % config.accounts.length];
        const ip = `${ips[index]}.${ips[index + 1]}.${ips[index + 2]}.${ips[index + 3]}`;
        const port = ips[index + 4] * 256 + ips[index + 5];
        const slp = await ping(ip, port, 0);
        if (typeof slp == 'string' || slp?.version?.protocol == null || typeof slp?.version?.protocol != 'number') return;
        let version = mcData(slp?.version?.protocol)?.version?.minecraftVersion;
        if (version == null || !protocol.supportedVersions.includes(version)) return;
        let result;
        try {
            result = await join(account, ip, port, version);
        } catch (err) {
            // console.log(`Bot error on ${ip}:${port}`, err);
            result = null;
        }
        while (result == 'retry') {
            await new Promise(res => setTimeout(res, 10000));
            try {
                result = await join(account, ip, port, version);
            } catch (err) {
                // console.log(`Bot error on ${ip}:${port} ${slp.version.protocol}`, err);
                result = null;
            }
        }
        if (result == null) return;
        console.log(`${ip}:${port} ${result}`);
        await client.query('UPDATE SERVERS SET whitelisted = $1 WHERE ip = $2 AND port = $3', [result, ip.split('.').reverse().map((a, i) => parseInt(a) * 256**i).reduce((a, b) => a + b, 0) - 2147483648, port - 32768]);
    }

    for (let i = startIndex; i < ips.length; i += 6) await check(i);
    for (let i = 0; i < startIndex; i += 6) await check(i);
}

(async () => {
    for (const account of config.accounts) await refreshToken(account);
    scan();
})()