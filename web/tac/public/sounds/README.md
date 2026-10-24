# 戰術地圖音效素材

目前**沒有任何外部素材**：所有聲音都由 `src/audio.ts` 用程式合成（槍聲依武器、爆炸、閃光彈、彈著、無線電提示音、警示音、環境底噪）。

要換成錄音素材時：

1. 把音檔放在這個目錄（`.ogg` 或 `.mp3`）。
2. 在 `sounds.json` 列出要取代的聲音（沒列的繼續用合成音）：

   ```json
   { "gun:rifle": "rifle.ogg", "gun:lmg": "lmg.ogg", "gun:dmr": "dmr.ogg", "gun:shotgun": "shotgun.ogg",
     "boom": "grenade.ogg", "flash": "flashbang.ogg",
     "impact:hard": "impact_concrete.ogg", "impact:dirt": "impact_dirt.ogg", "impact:flesh": "impact_flesh.ogg", "impact:soft": "impact_soft.ogg",
     "radio": "radio_chirp.ogg", "alert": "alert.ogg", "ambient": "ambient_city.ogg" }
   ```
3. 在下面的表記下每個檔案的來源與授權（CC0 優先；CC-BY 要在遊戲裡標註作者）。

| 檔案 | 來源 | 作者 | 授權 |
|---|---|---|---|
