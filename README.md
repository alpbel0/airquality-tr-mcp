# airquality-tr-mcp

Türkiye'nin resmi Ulusal Hava Kalitesi İzleme Ağı (UHKİA) verisini bir MCP
(Model Context Protocol) sunucusu olarak sunar; Claude Desktop gibi
MCP-uyumlu bir asistan, Türkiye'deki 81 il ve 365+ istasyon için gerçek
zamanlı ve geçmişe dönük hava kalitesi sorularını yanıtlayabilir.

**Bu, resmi olmayan (unofficial) bir entegrasyondur.** T.C. Çevre,
Şehircilik ve İklim Değişikliği Bakanlığı ile bir bağlantısı yoktur ve
onlar tarafından desteklenmemektedir. Kaynak portalın kullanım şartlarına
uymak kullanıcının sorumluluğundadır.

## Veri kaynakları

- **Hava kalitesi ölçümleri:** UHKİA (`sim.csb.gov.tr`) — resmi, dokümante
  edilmemiş ama halka açık uç noktalar üzerinden. Sunucu, kaynağın döndürdüğü
  HKİ (Hava Kalitesi İndeksi), kategori ve baskın kirletici değerlerini
  **olduğu gibi** kullanır; kendi hesaplaması, ortalaması veya tahmini
  yapılmaz.
- **Konum çözümleme (geocoding):** HeiGIT tarafından barındırılan Pelias
  (`https://api.heigit.org/pelias/v1/search`) — yalnızca metinle verilen bir
  konumu enlem/boyluma çevirmek için kullanılır, hava kalitesi verisi
  sağlamaz. Koordinat girişi bu servisi hiç çağırmaz.

## Gereksinimler

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Kurulum

Depoyu indirdikten veya klonladıktan sonra:

```bash
cd airquality-tr-mcp
uv sync
```

## Claude Desktop / Codex yapılandırması (stdio)

```json
{
  "mcpServers": {
    "airquality-tr": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/airquality-tr-mcp",
        "run",
        "python",
        "-m",
        "airquality_tr_mcp.server"
      ]
    }
  }
}
```

### Opsiyonel: metinle konum arama için API anahtarı

`get_nearest_air_quality` tool'unun `location` parametresi (ör.
`"Göbeklitepe, Şanlıurfa"`) HeiGIT/Pelias'a istek atar ve kişisel bir API
anahtarı gerektirir. `latitude`/`longitude` ile kullanım bu anahtarı hiç
gerektirmez ve diğer tüm tool'lar bu anahtar olmadan da tam çalışır.

```json
{
  "env": {
    "ORS_API_KEY": "YOUR_PERSONAL_KEY"
  }
}
```

**Anahtarınızı yalnızca yerel MCP yapılandırmasına ekleyin.** Sohbete
yapıştırmayın, kaynak koduna gömmeyin, GitHub'a commit'lemeyin. Anahtarı
`https://account.heigit.org/` üzerinden kendiniz oluşturursunuz.
Yapılandırmayı değiştirdikten sonra MCP istemcisini (Claude Desktop vb.)
**tamamen yeniden başlatmanız** gerekir.

## Tool'lar

| Tool | Amaç |
|---|---|
| `ping` | MCP sürecinin ayakta olup olmadığını kontrol eder |
| `list_stations` | Aktif UHKİA istasyonlarını listeler |
| `get_air_quality` | İl özeti ve istasyon bazlı döküm |
| `get_nearest_air_quality` | Metin veya koordinatla en yakın resmi istasyon verisi |
| `get_station_detail` | Tek bir istasyonun tam güncel ölçümü |
| `get_historical_data` | Günlük geçmiş özetleri |
| `get_trend_summary` | Kural tabanlı 3/6 günlük trend |
| `compare_cities` | İki il için kompakt karşılaştırma |
| `get_ranking` | İl seviyesinde sıralama |
| `get_detailed_ranking` | İstasyon seviyesinde ülke geneli sıralama |
| `get_health_advisory` | Kural tabanlı il sağlık tavsiyesi |
| `check_alert` | İstek üzerine HKİ/kirletici eşik kontrolü |

`get_air_quality` ve `get_nearest_air_quality` farklı amaçlara hizmet eder:

- **`get_air_quality`** — bir ilin özetini ve o ildeki **tüm** istasyonların
  dökümünü verir; il özetindeki temsili değer o ildeki en kötü (en yüksek
  HKİ'li) istasyondur.
- **`get_nearest_air_quality`** — bir konuma (metin veya koordinat) **en
  yakın** ve 75 km referans sınırı içinde geçerli HKİ ölçümü olan istasyonu
  bulur; il sınırlarıyla ilgilenmez.
- **`get_health_advisory`** — bir ilin temsili (en kötü istasyon) durumuna
  göre kural tabanlı, deterministik tavsiye üretir.
- **`get_ranking`** vs **`get_detailed_ranking`** — `get_ranking` il başına
  tek temsili değerle hızlı bir sıralama verir; `get_detailed_ranking` aynı
  veriyi il bazında özetlemeden, istasyon seviyesinde ülke geneli sıralar.

### `get_nearest_air_quality` örnekleri

```text
get_nearest_air_quality(location="Göbeklitepe, Şanlıurfa")
get_nearest_air_quality(latitude=37.2232, longitude=38.9224)
```

## Gizlilik, atıf ve sınırlamalar

- Metinle konum sorguları HeiGIT/Pelias'a gönderilir; koordinat girişi bu
  aktarımı tamamen atlar.
- Hava kalitesi ölçümleri UHKİA'dan gelir.
- Mesafe, yerel düz-hat (Haversine) hesabıdır — yol/rota mesafesi değildir.
- `referans_hki` yerel bir tahmin, enterpolasyon veya ortalama değildir;
  ilgili istasyonun kaynaktaki resmi değeridir.
- İki saatten eski bir ölçüm, açık bir uyarıyla birlikte yine de döndürülür.
- Bu paket yerel bir stdio yazılımıdır; v1'de merkezi/barındırılan bir servis
  yoktur.
- Kullanıcılar kendi HeiGIT API anahtarlarını `https://account.heigit.org/`
  üzerinden edinir.
- HeiGIT/Pelias ve UHKİA birbirinden bağımsız, ilişkisiz iki dış servistir.
