const axios = require('axios');
const path = require('path');

const EMBY_LIBRARY_ID = 8341;
const EMBY_URL = "http://embyserver:8096";
const EMBY_API_KEY = "d26d60b37d334aaa981533daccd7b74a";
const EMBY_ADMIN_USER = "09e1241ab43a403fb1d110ba302defe7";
const RADARR_URL = "http://radarr:7878";
const RADARR_API_KEY = "d3d5669dbdb3402c8e0be2ced87e265e";
const PROXY_CONF = {
    host: "192.168.1.208",
    port: 8676
}

const emby = axios.create({
    baseURL: `${EMBY_URL}/emby`,
    proxy: PROXY_CONF ? PROXY_CONF : {},
    headers: {
        "X-Emby-Token": EMBY_API_KEY
    }
});

const radarr = axios.create({
    baseURL: `${RADARR_URL}/api/v3`,
    proxy: PROXY_CONF ? PROXY_CONF : {},
    headers: {
        "X-Api-Key": RADARR_API_KEY
    }
});

const outFilePath = process.argv.find(arg => arg.startsWith("--output=")).split("--output=")[1];
const tmdbId = path.basename(outFilePath).match(/\[tmdb-(\d+)\]/)[1];
const movieFolder = path.dirname(outFilePath);

const main = async () => {
    if (path.extname(outFilePath) !== '.mkv') return console.log(`${outFilePath} is not a movie file`);

    try { 
        const addedAt = (await radarr(`/movie?tmdbId=${tmdbId}`)).data[0].added;

        console.log(`Found ${outFilePath} added to radarr at ${addedAt}`);

        const embyItemDirId = (
            await emby(`/Items?ParentId=${EMBY_LIBRARY_ID}&Fields=Path`)
        ).data.Items.find(item => movieFolder === item.Path).Id;

        console.log(`Found movie folder with ID: ${embyItemDirId}`);

        const embyItemId = (
            await emby(`/Items?ParentId=${embyItemDirId}&Fields=Path`)
        ).data.Items.find(item => outFilePath === item.Path).Id;

        console.log(`Found emby movie item with ID: ${embyItemId}`);

        const metadataInfo = (
            await emby(`/Users/${EMBY_ADMIN_USER}/Items/${embyItemId}?Fields=ChannelMappingInfo&ExcludeFields=Chapters,MediaSources,MediaStreams,Subviews`)
        ).data

        metadataInfo.DateCreated = addedAt;
        metadataInfo.DateModified = addedAt;

        await emby.post(`/Items/${embyItemId}`, metadataInfo);

        console.log('Successfully updated added at time');
    } catch (err) {
        if (err.response && err.response.status === 400) {
            console.log(`No radarr item found for ${outFilePath}}. Import time will not be saved.`);
            console.error(err);
        } else {
            console.error(err);
        }
    }
}

console.log('Starting import time saver');
main();