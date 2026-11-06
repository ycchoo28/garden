// Words, numbers and dates for claims. The API sends statuses, states and outcomes as codes and amounts as strings.

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  submitted: 'Submitted',
  approved: 'Approved',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
};

const RUN_STATE_LABELS: Record<string, string> = {
  pending: 'Awaiting decision',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Withdrawn',
};

const ROLE_LABELS: Record<string, string> = {
  manager: 'Manager',
  finance: 'Finance',
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export function runStateLabel(state: string): string {
  return RUN_STATE_LABELS[state] ?? state;
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

export function categoryLabel(category: string): string {
  return category.charAt(0).toUpperCase() + category.slice(1);
}

// Amounts arrive as two-decimal strings, such as "120.00". Thousands are grouped for reading.
export function formatAmount(amount: string): string {
  return new Intl.NumberFormat('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(amount));
}

export function formatDateTime(timestamp: string): string {
  return new Date(timestamp).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
}
