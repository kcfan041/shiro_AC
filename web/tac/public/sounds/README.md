# 戰術地圖音效素材

`sounds.json` 列出的聲音用這裡的錄音，沒列的繼續用 `src/audio.ts` 程式合成的（閃光彈、彈著、無線電提示音、警示音、環境底噪）。
目前取代了四種槍聲與手榴彈爆炸；彈著與無線電沒有找到合用的 CC0 素材，仍是合成音。

- 每個檔案是原始錄音的一段：槍聲取第一發起音前 10 ms 開始的一段，後 40% 淡出，轉成單聲道 48 kHz 16 bit、峰值正規化。
  播放時引擎再依距離衰減、低通、延遲、左右聲道，音高隨機 ±5%（`src/audio.ts`）。
- 換素材：把音檔放進這個目錄（`.wav`、`.ogg`、`.mp3`、`.flac` 都可以），改 `sounds.json`，在下表記下來源與授權（CC0 優先；CC-BY 要在遊戲裡標註作者）。
  可以取代的鍵：`gun:rifle`、`gun:lmg`、`gun:dmr`、`gun:shotgun`、`boom`、`flash`、`impact:hard`、`impact:dirt`、`impact:flesh`、`impact:soft`、`radio`、`alert`、`ambient`。

| 檔案 | 用在 | 來源 | 作者 | 授權 |
|---|---|---|---|---|
| `rifle.wav` | 步槍 | [The Free Firearm Sound Library](https://opengameart.org/content/the-free-firearm-sound-library)：`AR-15/D_32P.wav`（AR-15，5.56，近距、單發） | Ben Jaszczak、Brian Nelson、Kevin Heras、Matthew Nanney | CC0 |
| `lmg.wav` | 機槍 | 同上：`AK-47/C_28P.wav`（AK-47，7.62×39，近距、單發） | 同上 | CC0 |
| `dmr.wav` | 射手步槍 | 同上：`Tikka/W_29P.wav`（Tikka T3，.30-06，近距） | 同上 | CC0 |
| `shotgun.wav` | 霰彈槍 | 同上：`Nova/O_21P.wav`（Benelli Nova，12 號，近距） | 同上 | CC0 |
| `grenade.wav` | 手榴彈爆炸 | [mechanical explosion](https://opengameart.org/content/mechanical-explosion)：`mechanical_explosion.wav` | Spring Spring | CC0 |

沒有採用的：[gunshot sounds](https://opengameart.org/content/gunshot-sounds)（Tabasco）頁面標 CC0，但壓縮檔裡的授權檔寫 CC-BY 3.0（Vincent Sevedge），授權不一致所以不用；
[synthesized explosion](https://opengameart.org/content/synthesized-explosion)（qubodup，CC0）是另一個爆炸候選，目前用 mechanical explosion。
