export const CODE_LABELS: Record<string, string> = {
  missing_ledger_adjustment: "Missing ledger adjustment",
  commission_mismatch: "Commission mismatch",
  fx_discrepancy: "FX discrepancy",
  duplicate_ledger_entry: "Duplicate ledger entry",
  awaiting_evidence: "Awaiting evidence",
  disbursement_mismatch: "Disbursement mismatch",
  unresolvable_commission_rule: "Unresolvable commission rule",
  mixed_currency_order: "Mixed-currency order",
  refund_exceeds_share: "Refund exceeds share",
  refund_unattributable: "Refund unattributable",
  duplicate_provider_record: "Duplicate provider record",
};

export const label = (code: string): string => CODE_LABELS[code] ?? code;

export const formatDate = (iso: string): string =>
  new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(iso));

export const formatDateTime = (iso: string): string =>
  new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
