export const RETIRED_ARMS = new Set(['marwil_gnn'])

export function isRetiredArm(arm: string | null | undefined): boolean {
  return arm != null && RETIRED_ARMS.has(arm)
}
