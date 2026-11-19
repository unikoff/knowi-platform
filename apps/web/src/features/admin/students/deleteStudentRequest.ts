import { ApiError } from "../../../shared/api/client";

export type DeletionTarget = { id: number; accountRef?: string };
export type DeletionResult = "pending" | "complete";
const apiPrefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;

// One request only. A failed or interrupted request has an unknown outcome;
// callers may reconcile only with the original lifecycle reference.
export async function deleteStudentWithStatus(target: DeletionTarget, signal: AbortSignal): Promise<DeletionResult> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal.aborted) abort(); else signal.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 12_000);
  try {
    if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
    const response = await fetch(`${apiPrefix}/admin/users/${target.id}`, {
      method: "DELETE", credentials: "include", cache: "no-store", redirect: "error", signal: controller.signal,
      headers: { Accept: "application/json", ...(target.accountRef ? { "X-Account-Ref": target.accountRef } : {}) },
    });
    if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
    if (response.status === 204) return "complete";
    if (response.status === 200 || response.status === 202) {
      if (!response.headers.get("content-type")?.toLowerCase().includes("application/json")) throw new Error("Не удалось подтвердить результат удаления.");
      let body: unknown;
      try { body = await response.json(); } catch { throw new Error("Не удалось подтвердить результат удаления."); }
      if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
      if (typeof body !== "object" || body === null) throw new Error("Не удалось подтвердить результат удаления.");
      if (response.status === 202 && "status" in body && body.status === "pending") return "pending";
      if (response.status === 200 && "deleted" in body && body.deleted === target.id) return "complete";
      throw new Error("Не удалось подтвердить результат удаления.");
    }
    let detail = "Не удалось проверить результат удаления.";
    if (response.headers.get("content-type")?.toLowerCase().includes("application/json")) {
      try { const body: unknown = await response.json(); if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") detail = body.detail; } catch { /* Keep a generic error. */ }
    }
    throw new ApiError(detail, response.status);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}
