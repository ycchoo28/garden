// Turns an error from the API, an RFC 9457 problem document, into a sentence for the screen.
export function problemMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null) {
    if ('detail' in error && typeof error.detail === 'string') return error.detail;
    if ('title' in error && typeof error.title === 'string') return error.title;
  }
  return 'Something went wrong. Try again.';
}

// True when the API refused the request because there is no signed-in session.
export function isUnauthorized(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error && error.status === 401;
}
