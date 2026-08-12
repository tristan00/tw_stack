import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merge class names so a caller's override actually wins over a component default. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
