// Agency JSON API istemcisi. Oturum cookie'si (SSO sonrası Flask session) ile
// aynı origin'de çalışır. Mutasyonlarda CSRF token'ı /api/session'dan gelir.
const BASE = "/api"

// Yükleme üst sınırı: dosya başına 500 MB (backend uçlarıyla aynı; sunucu tarafında da
// zorlanır). Frontend ön kontrolü: sınırı aşan dosyalar boşuna yüklenmesin.
export const MAX_UPLOAD_MB = 500
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

// Dosya kabul kontrolü: MIME + uzantı. Bazı dosyalar (macOS, iPhone HEIC,
// sürükle-bırak, yeniden adlandırılmış) geçerli görsel/video olsa da boş/yanlış
// MIME raporlar; yalnız MIME'a bakınca sessizce eleniyor, "yüklendi" sanılıp
// Drive'a hiç gitmiyorlardı. Uzantı fallback'i bu sessiz kaybı kapatır.
const EXT_OK: Record<"image" | "video", RegExp> = {
  image: /\.(jpe?g|png|webp|gif|heic|heif|tiff?|bmp)$/i,
  video: /\.(mp4|mov|m4v|webm|avi|mkv)$/i,
}
export function acceptsFile(f: File, kind: "image" | "video") {
  return f.type.startsWith(`${kind}/`) || EXT_OK[kind].test(f.name)
}

let csrfToken = ""
export function setCsrf(t: string) {
  csrfToken = t
}

class ApiError extends Error {
  status: number
  retriable: boolean
  constructor(message: string, status: number, retriable = false) {
    super(message)
    this.status = status
    this.retriable = retriable
  }
}

async function parse(res: Response) {
  const text = await res.text()
  let data: any = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!res.ok) {
    const msg = (data && (data.message || data.error)) || `Hata (${res.status})`
    throw new ApiError(msg, res.status)
  }
  return data
}

export async function apiGet(path: string) {
  const res = await fetch(BASE + path, {
    credentials: "same-origin",
    headers: { "X-Requested-With": "XMLHttpRequest" },
  })
  return parse(res)
}

export async function apiJson(path: string, body: unknown, method = "POST") {
  const res = await fetch(BASE + path, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify(body),
  })
  return parse(res)
}

// Multipart yükleme (dosya) + yükleme ilerlemesi. XHR kullanır (fetch upload
// progress vermez). onProgress: 0-100 yüzde. Her yükleme UI'ı progress bar için.
// Tek XHR denemesi. status 0 (ağ/transport kopması) → `retriable=true` işaretli
// ApiError ile reddeder; çağıran (varsayılan davranışta) yeniden dener.
// ÖNEMLİ: status 0, "istek sunucuya HİÇ ulaşmadı" ANLAMINA GELMEZ — XHR
// `onerror`, gövde TAMAMEN gönderildikten SONRA (sunucu isteği işlerken/işini
// bitirip yanıt dönerken) bağlantı koparsa da tetiklenir; tarayıcı bu iki
// durumu ayırt etmez. Yani sunucu isteği commit'lemiş olabilir. Bu yüzden
// otomatik retry yalnız IDEMPOTENT uçlar için güvenlidir (aynı gövdeyi tekrar
// göndermek yan etkiyi tekrarlamaz); idempotent OLMAYAN uçlar (POST ile her
// çağrıda yeni bir kayıt/sürüm doğan yükleme uçları gibi) `retry=false` ile
// çağırmalı — aksi halde bağlantı yanıt dönerken koparsa aynı içerik ikinci
// kez commit'lenir (bkz. design-files.ts: `useUploadDesignFile`/`useUploadVersion`).
function uploadAttempt(path: string, form: FormData, onProgress?: (pct: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open("POST", BASE + path)
    xhr.withCredentials = true
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest")
    xhr.setRequestHeader("X-CSRFToken", csrfToken)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      let data: any = null
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        data = null
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data)
      } else {
        const msg = (data && (data.message || data.error)) || `Hata (${xhr.status})`
        // 502/503/504 = geçici sunucu/ağ katmanı → yeniden denenebilir.
        reject(new ApiError(msg, xhr.status, xhr.status >= 502 && xhr.status <= 504))
      }
    }
    xhr.onerror = () => reject(new ApiError("Ağ hatası", 0, true))
    xhr.ontimeout = () => reject(new ApiError("Zaman aşımı", 0, true))
    xhr.send(form)
  })
}

// Yükleme + geçici hatada otomatik yeniden deneme. Sunucu↔Google arası anlık
// kopmalar (bu ortamda görülüyor) isteği YANIT DÖNMEDEN düşürebiliyordu — bu,
// isteğin sunucuya ulaşmadığı anlamına gelmez (yukarıdaki not), ama panel
// tarafında sonucu bilmenin tek yolu retry'dı. Backoff: 1s, 3s. `retry=false`
// geçilirse (idempotent olmayan uçlar) tek deneme yapılır, hiç beklemez.
export async function apiUpload(
  path: string,
  form: FormData,
  onProgress?: (pct: number) => void,
  retry = true,
): Promise<any> {
  // Çağıranların EZİCİ çoğunluğu idempotent (retriable) uçlara yükler; bu yüzden
  // varsayılan `true` kalır ve mevcut çağırım yerleri bozulmaz. `retry=false`
  // yalnız idempotent OLMAYAN uçlarda geçilir (yukarıdaki not).
  const backoffs = retry ? [1000, 3000] : []
  for (let attempt = 0; ; attempt++) {
    try {
      return await uploadAttempt(path, form, onProgress)
    } catch (e) {
      if (e instanceof ApiError && e.retriable && attempt < backoffs.length) {
        onProgress?.(0)
        await new Promise((r) => setTimeout(r, backoffs[attempt]))
        continue
      }
      throw e
    }
  }
}

// Gövdeli DELETE (soft-delete reason'ı için) — CSRF header'lı.
export async function apiDelete(path: string, body?: unknown) {
  const res = await fetch(BASE + path, {
    method: "DELETE",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "X-CSRFToken": csrfToken,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return parse(res)
}

// Gövdeli POST → ikili yanıt (zip vb.) blob olarak döner. Hata durumunda JSON
// error mesajını okumaya çalışır.
export async function apiBlob(path: string, body: unknown): Promise<Blob> {
  const res = await fetch(BASE + path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
      "X-CSRFToken": csrfToken,
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let msg = `Hata (${res.status})`
    try {
      const d = JSON.parse(await res.text())
      msg = d.message || d.error || msg
    } catch { /* ikili değil, varsayılan mesaj */ }
    throw new ApiError(msg, res.status)
  }
  return res.blob()
}

export { ApiError }
