# airquality-tr-mcp

Türkiye'nin resmi Ulusal Hava Kalitesi İzleme Ağı (UHKİA) verisini bir MCP
(Model Context Protocol) sunucusu olarak sunar; Claude Desktop gibi
MCP-uyumlu bir asistan, Türkiye'deki 81 il ve 365+ istasyon için gerçek
zamanlı ve geçmişe dönük hava kalitesi sorularını yanıtlayabilir.

**Güncel kararlı sürüm:** `v1.1.0`

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
- **Konum çözümleme (geocoding):** OpenStreetMap Nominatim
  (`https://nominatim.openstreetmap.org/search`) — yalnızca metinle verilen
  bir konumu enlem/boyluma çevirmek için kullanılır, hava kalitesi verisi
  sağlamaz, API anahtarı gerektirmez. Koordinat girişi bu servisi hiç
  çağırmaz.

## Gereksinimler

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Kurulum

PyPI üzerinden (önerilen):

```bash
pip install airquality-tr-mcp
# veya
uvx airquality-tr-mcp
```

Depoyu klonlayarak geliştirme amaçlı kurulum:

```bash
cd airquality-tr-mcp
uv sync
```

## Claude Desktop / Codex yapılandırması (stdio)

PyPI paketiyle kurulduysa:

```json
{
  "mcpServers": {
    "airquality-tr": {
      "command": "uvx",
      "args": ["airquality-tr-mcp"]
    }
  }
}
```

Depodan klonlanmış geliştirme kurulumuyla:

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

## REST API olarak çalıştırma

MCP sunucusuyla aynı iş mantığını kullanan bağımsız bir REST API de mevcuttur:

```bash
uv run airquality-tr-api
# veya
uv run uvicorn airquality_tr_mcp.api:app --reload
```

Varsayılan olarak `http://localhost:8000` üzerinde dinler. Etkileşimli
dokümantasyon için `http://localhost:8000/docs` adresini ziyaret edin.
Uç noktalar MCP tool'larıyla bire bir eşlenir (`/stations`,
`/air-quality`, `/nearest-air-quality`, `/station`, `/historical-data`,
`/trend-summary`, `/compare-cities`, `/ranking`, `/detailed-ranking`,
`/health-advisory`, `/alert`) ve aynı Türkçe JSON gövdelerini döndürür;
tek fark, hata durumlarında (`hata` alanı dolu yanıtlarda) uygun bir
HTTP durum kodu (400/404/409/429/502/504) döndürülmesidir.

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

### Örnek çağrılar

```text
ping()

list_stations()
list_stations(province="İstanbul")
list_stations(province="Kadıköy")

get_air_quality(province="Ankara")
get_air_quality(province="İstanbul", district="Kadıköy")

get_station_detail(station="Ankara - Çankaya")
get_station_detail(station="a1b2c3d4-e5f6-7890-abcd-ef1234567890")

get_historical_data(province="İzmir", days=7)
get_historical_data(province="İstanbul", days=14, district="Kadıköy")

get_trend_summary(province="Bursa", days=3)
get_trend_summary(province="Bursa", days=6)

compare_cities(province1="İstanbul", province2="Ankara")

get_ranking(mode="worst", limit=10)
get_ranking(mode="best", limit=5)

get_detailed_ranking(mode="worst", limit=20)

get_health_advisory(province="Kocaeli")

check_alert(province="İstanbul", threshold=100)
check_alert(province="Ankara", threshold=50, pollutant="PM10")
check_alert(province="İzmir", threshold=40, pollutant="PM10", district="Konak")
```

### `get_nearest_air_quality` örnekleri

```text
get_nearest_air_quality(location="Göbeklitepe, Şanlıurfa")
get_nearest_air_quality(latitude=37.2232, longitude=38.9224)
```

## Gizlilik, atıf ve sınırlamalar

- Metinle konum sorguları OpenStreetMap Nominatim'e gönderilir; koordinat
  girişi bu aktarımı tamamen atlar.
- Hava kalitesi ölçümleri UHKİA'dan gelir.
- Mesafe, yerel düz-hat (Haversine) hesabıdır — yol/rota mesafesi değildir.
- `referans_hki` yerel bir tahmin, enterpolasyon veya ortalama değildir;
  ilgili istasyonun kaynaktaki resmi değeridir.
- İki saatten eski bir ölçüm, açık bir uyarıyla birlikte yine de döndürülür.
- Bu paket yerel bir stdio yazılımıdır; v1'de merkezi/barındırılan bir servis
  yoktur.
- `location` parametresi il/ilçe yazım hatalarını `get_air_quality` kadar
  iyi tolere etmez (Nominatim serbest metin araması yapar, il/ilçe fuzzy
  düzeltmesi yapmaz); sadece bir ilin/ilçenin genel hava kalitesi
  soruluyorsa `get_air_quality`/`list_stations` tercih edilmelidir.
- Nominatim/OpenStreetMap ve UHKİA birbirinden bağımsız, ilişkisiz iki dış
  servistir.

## Lisans

Bu proje [MIT Lisansı](LICENSE) ile lisanslanmıştır.
