# 戰術地圖音效素材

`sounds.json` 列出的聲音用這裡的錄音，沒列的用 `src/audio.ts` 程式合成的（彈殼落地、閃光彈、彈著、無線電提示音、警示音、環境底噪）。
怎麼分得出槍種與陣營（節奏、兩套錄音、機械聲）見 `docs/interface.md` §3.4；試聽頁 `/static/dist/sound.html`。

- 槍聲：原始錄音取第一發起音前 10 ms 開始的一段，後 40% 淡出，轉成單聲道 48 kHz 16 bit、峰值正規化。機槍是同一把步槍的錄音重新取樣降調 15%（聽起來較重）。
- 泵動：Model 12 錄音裡兩發之間的「往後拉」（4.17～4.37 秒）與「往前推」（4.67～4.92 秒），中間留 0.1 秒。
- 播放時引擎再依距離衰減、低通、延遲、左右聲道，音高隨機 ±2%。
- 鍵：`gun:own:<武器>`、`gun:enemy:<武器>`（武器：`rifle`、`lmg`、`dmr`、`shotgun`；找不到就用合成的 `gun:<武器>`）、`mech:pump`、`mech:casing`、`boom`、`flash`、`impact:hard`、`impact:dirt`、`impact:flesh`、`impact:soft`、`radio`、`alert`、`ambient`。
- 換素材：把音檔放進這個目錄（`.wav`、`.ogg`、`.mp3`、`.flac` 都可以），改 `sounds.json`，在下表記下來源與授權（CC0 優先；CC-BY 要在遊戲裡標註作者）。

下表的「槍聲庫」是 [The Free Firearm Sound Library](https://opengameart.org/content/the-free-firearm-sound-library)（Ben Jaszczak、Brian Nelson、Kevin Heras、Matthew Nanney，CC0），括號是庫裡的檔案。

| 檔案 | 用在 | 來源 | 授權 |
|---|---|---|---|
| `own_rifle.wav` | 我方步槍 | 槍聲庫（`AR-15/D_32P.wav`，AR-15，5.56，近距單發） | CC0 |
| `own_lmg.wav` | 我方機槍 | 同上，降調 15% | CC0 |
| `own_dmr.wav` | 我方射手步槍 | 槍聲庫（`Tikka/W_29P.wav`，Tikka T3，.30-06，近距） | CC0 |
| `own_shotgun.wav` | 我方霰彈槍 | 槍聲庫（`Nova/O_21P.wav`，Benelli Nova，12 號，近距） | CC0 |
| `enemy_rifle.wav` | 敵方步槍 | 槍聲庫（`AK-47/C_28P.wav`，AK-47，7.62×39，近距單發） | CC0 |
| `enemy_lmg.wav` | 敵方機槍 | 同上，降調 15% | CC0 |
| `enemy_dmr.wav` | 敵方射手步槍 | 槍聲庫（`Mosin Nagant/M_21P.wav`，Mosin Nagant，7.62×54，近距） | CC0 |
| `enemy_shotgun.wav` | 敵方霰彈槍 | 槍聲庫（`Model 12/K_22P.wav`，Winchester Model 12，12 號，近距） | CC0 |
| `pump.wav` | 霰彈槍泵動 | 槍聲庫（`Model 12/K_22P.wav` 兩發之間） | CC0 |
| `grenade.wav` | 手榴彈爆炸 | [mechanical explosion](https://opengameart.org/content/mechanical-explosion)（Spring Spring） | CC0 |

沒有採用的：[gunshot sounds](https://opengameart.org/content/gunshot-sounds)（Tabasco）頁面標 CC0，但壓縮檔裡的授權檔寫 CC-BY 3.0（Vincent Sevedge），授權不一致所以不用；
[synthesized explosion](https://opengameart.org/content/synthesized-explosion)（qubodup，CC0）是另一個爆炸候選，目前用 mechanical explosion。
