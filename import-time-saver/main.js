const axios = require('axios');
const path = require('path');

const EMBY_LIBRARY_ID = 8341;
const EMBY_URL = "http://embyserver:8096";
const EMBY_API_KEY = "";
const EMBY_ADMIN_USER = "";
const RADARR_URL = "http://radarr:7878";
const RADARR_API_KEY = "";
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

const main = async () => {
    const outFilePath = process.argv.find(arg => arg.startsWith("--output=")).split("--output=")[1];
    const movieFolder = path.dirname(outFilePath);
    const tmdbId = path.basename(outFilePath).match(/\[tmdb-(\d+)\]/)?.[1];

    if (!tmdbId) return console.log(`${outFilePath} does not contain a TMDB ID. Unable to associate with Radarr`);
    if (path.extname(outFilePath) !== '.mkv') return console.log(`${outFilePath} is not a movie file`);

    try { 
        const radarrItem = (await radarr(`/movie?tmdbId=${tmdbId}`)).data[0];
        if (!radarrItem) return console.log(`${tmdbId} could not be found in radarr`);

        const addedAt = radarrItem.added;

        console.log(`Found ${outFilePath} added to radarr at ${addedAt}`);

        const embyItemDir = (
            await emby(`/Items?ParentId=${EMBY_LIBRARY_ID}&Fields=Path`)
        ).data.Items.find(item => movieFolder === item.Path);

        if(!embyItemDir) return console.log(`Could not find emby folder for movie at path ${movieFolder}. Likely a new movie item.`);

        console.log(`Found movie folder with ID: ${embyItemDir.Id}`);

        const embyItemId = (
            await emby(`/Items?ParentId=${embyItemDir.Id}&Fields=Path`)
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