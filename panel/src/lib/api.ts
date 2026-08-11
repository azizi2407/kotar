// Agency JSON API client. Runs on the same origin as the session cookie (Flask
// session set after SSO). The CSRF token for mutations comes from /api/session.
const BASE = "/api"

// Upload size cap: 500 MB per file (matches the backend endpoints; also enforced
// server-side). Frontend pre-check so files over the limit aren't uploaded for nothing.
export const MAX_UPLOAD_MB = 500
export const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

// File acceptance check: MIME + extension. Some files (macOS, iPhone HEIC,
// drag-and-drop, renamed files) are valid images/videos but report an empty or
// wrong MIME type; checking MIME alone silently rejected them — they'd be assumed
// "uploaded" while never actually reaching Drive. The extension fallback closes this silent gap.
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
    const msg = (data && (data.message || data.error)) || `Error (${res.status})`
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

// Multipart upload (file) + upload progress. Uses XHR (fetch doesn't provide upload
// progress). onProgress: 0-100 percent. Every upload UI uses this for its progress bar.
// A single XHR attempt. On status 0 (network/transport drop) → rejects with an
// ApiError flagged `retriable=true`; the caller retries (in the default behavior).
// IMPORTANT: status 0 does NOT MEAN "the request never reached the server AT ALL" —
// XHR's `onerror` also fires if the connection drops AFTER the body was FULLY sent
// (while the server is processing the request, or finishing up and returning the
// response); the browser doesn't distinguish between these two cases. So the server
// may have already committed the request. That's why automatic retry is only safe for
// IDEMPOTENT endpoints (resending the same body doesn't repeat the side effect);
// endpoints that are NOT idempotent (upload endpoints where each POST call spawns a
// new record/version) must be called with `retry=false` — otherwise if the connection
// drops while the response is coming back, the same content gets committed a second
// time (see design-files.ts: `useUploadDesignFile`/`useUploadVersion`).
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
        const msg = (data && (data.message || data.error)) || `Error (${xhr.status})`
        // 502/503/504 = transient server/network layer → retriable.
        reject(new ApiError(msg, xhr.status, xhr.status >= 502 && xhr.status <= 504))
      }
    }
    xhr.onerror = () => reject(new ApiError("Network error", 0, true))
    xhr.ontimeout = () => reject(new ApiError("Timeout", 0, true))
    xhr.send(form)
  })
}

// Upload + automatic retry on a transient error. Momentary server↔Google
// disconnects (observed in this environment) could drop the request BEFORE a
// response came back — this doesn't mean the request never reached the server
// (see note above), but on the panel side, retrying was the only way to find out
// the outcome. Backoff: 1s, 3s. If `retry=false` is passed (for non-idempotent
// endpoints), a single attempt is made with no waiting.
export async function apiUpload(
  path: string,
  form: FormData,
  onProgress?: (pct: number) => void,
  retry = true,
): Promise<any> {
  // The OVERWHELMING majority of callers upload to idempotent (retriable) endpoints;
  // that's why the default stays `true` and existing call sites keep working. `retry=false`
  // is only passed for endpoints that are NOT idempotent (see note above).
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

// DELETE with a body (for the soft-delete reason) — with a CSRF header.
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

// POST with a body → returns a binary response (zip etc.) as a blob. On error,
// tries to read the JSON error message.
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
    let msg = `Error (${res.status})`
    try {
      const d = JSON.parse(await res.text())
      msg = d.message || d.error || msg
    } catch { /* not binary, use the default message */ }
    throw new ApiError(msg, res.status)
  }
  return res.blob()
}

export { ApiError }
