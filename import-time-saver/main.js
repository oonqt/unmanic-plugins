const axios = require('axios');

const EMBY_URL = "";
const EMBY_API_KEY = "";
const MERGERFS_DISK_1_PATH = "";
const MERGERFS_DISK_2_PATH = ""

const embyClient = axios.create({
    baseURL: `${EMBY_URL}/emby`,
    headers: {
        "X-Emby-Token": EMBY_API_KEY
    }
});

const outFilePath = process.argv.find(arg => arg.startsWith("--output=")).split("--output=")[1];

console.log('Starting import time saver');

