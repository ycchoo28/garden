// Turns an error from the API, an RFC 9457 problem document, into a sentence for the screen.
export function problemMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null) {
    if ('detail' in error && typeof error.detail === 'string') return error.detail;
    if ('title' in error && typeof error.title === 'string') return error.title;
  }
  return 'Something went wrong. Try again.';
}

// The field-level messages of a problem document, such as "rows[3].amount_usd: Enter an amount...".
export function problemFieldMessages(error: unknown): string[] {
  if (typeof error !== 'object' || error === null || !('errors' in error)) return [];
  const { errors } = error;
  if (!Array.isArray(errors)) return [];
  return errors.flatMap((item: unknown) => {
    if (typeof item !== 'object' || item === null) return [];
    const field = 'field' in item && typeof item.field === 'string' ? item.field : '';
    const message = 'message' in item && typeof item.message === 'string' ? item.message : '';
    return message === '' ? [] : [field === '' ? message : `${field}: ${message}`];
  });
}

// True when the API refused the request because there is no signed-in session.
export function isUnauthorized(error: unknown): boolean {
  return typeof error === 'object' && error !== null && 'status' in error && error.status === 401;
}
