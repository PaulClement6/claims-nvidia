export const send = <T,>(url: string, init?: RequestInit) =>
  fetch(url, init).then((r) => (r.ok ? (r.json() as Promise<T>) : Promise.reject(new Error(String(r.status)))))

export const json = (method: string, body: object): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})
