// Every word a finance reader sees that is not a number or a record id.
//
// Written for the person who owns a marketplace's books, not for the engineer
// who built the matching. Where the engine says "expected" and "reported", this
// says who said what, because in this product "expected" is not "what should
// have happened" in every case and a reader cannot tell which is which.

export interface TypeCopy {
  /** The name of the problem, as a person would say it. */
  title: string;
  /** What it means, in one or two sentences. */
  meaning: string;
  /** What a person should do about it. */
  action: string;
  /** What the engine calls "expected" and "reported", per type. Absent where the
   *  finding is not a comparison of two amounts. */
  expectedLabel?: string;
  reportedLabel?: string;
}

export const TYPE_COPY: Record<string, TypeCopy> = {
  missing_ledger_adjustment: {
    title: "Refund not booked to the seller",
    meaning:
      "PayPal refunded the buyer, but the seller's ledger has no matching entry, so the seller's balance is overstated.",
    action:
      "Book the refund, and the commission that goes back with it, to the seller's ledger.",
  },
  commission_mismatch: {
    title: "Commission differs from the rule",
    meaning:
      "The commission the marketplace kept is not what its commission rule, and PayPal's platform fee, say it should be.",
    action:
      "Check the commission rule for this seller and category, then correct the ledger entry or the rule.",
    expectedLabel: "Commission per the rule",
    reportedLabel: "Marketplace kept",
  },
  fx_discrepancy: {
    title: "Currency conversion doesn't match PayPal",
    meaning:
      "After converting currency, the amount the marketplace recorded differs from what PayPal actually settled.",
    action:
      "Correct the ledger to PayPal's settled amount. Do not recalculate it with an exchange rate.",
    expectedLabel: "PayPal settled",
    reportedLabel: "Marketplace recorded",
  },
  duplicate_ledger_entry: {
    title: "Same payment booked twice",
    meaning:
      "One payment or refund appears twice in the seller's ledger under different references, so the seller is credited or debited twice.",
    action: "Reverse the duplicate entry.",
  },
  awaiting_evidence: {
    title: "Waiting for PayPal's record",
    meaning:
      "The marketplace has a record that PayPal has not reported yet. PayPal's reports run about a day behind, so this is usually just late, not wrong.",
    action: "Nothing yet. It is checked again on the next run.",
  },
  disbursement_mismatch: {
    title: "Seller payout doesn't match what they're owed",
    meaning:
      "PayPal released one amount and the seller's ledger shows another, and nothing still in progress explains the gap.",
    action:
      "Find the difference between the payout and the ledger, then correct the ledger or raise it with PayPal.",
    expectedLabel: "Seller is owed",
    reportedLabel: "Ledger records",
  },
  unresolvable_commission_rule: {
    title: "No clear commission rule",
    meaning:
      "The system could not work out which commission rule applies: none matches, two tie, or a rule is in the wrong currency.",
    action: "Fix the commission rules for this seller.",
  },
  mixed_currency_order: {
    title: "Order in more than one currency",
    meaning:
      "The order's lines are in different currencies, so there is no single figure to compare. The system will not invent an exchange rate.",
    action: "Review it by hand, using PayPal's settlement for each currency.",
    reportedLabel: "Ledger records",
  },
  refund_exceeds_share: {
    title: "Refund larger than the seller's share",
    meaning:
      "A refund is bigger than the seller's part of the payment it came out of.",
    action:
      "Check which seller the refund belongs to and whether the payment was split correctly.",
  },
  refund_unattributable: {
    title: "Can't tell which seller a refund belongs to",
    meaning:
      "The records do not show which seller a refund should come out of.",
    action:
      "Identify the seller from the order or the buyer's request, and record it.",
  },
  duplicate_provider_record: {
    title: "PayPal record received twice",
    meaning:
      "The same PayPal record arrived twice. It is counted once and flagged so you can check nothing was applied twice.",
    action: "Confirm the duplicate was ignored and no amount was booked twice.",
  },
};

const FALLBACK: TypeCopy = {
  title: "Exception",
  meaning: "The records do not agree.",
  action: "Open the evidence below and compare the records.",
};

export const typeCopy = (code: string): TypeCopy => TYPE_COPY[code] ?? FALLBACK;
export const typeTitle = (code: string): string => typeCopy(code).title;

// ---- Dates ----------------------------------------------------------------

export const ORDER_DATE_HINT = "When the customer placed the order.";

export const FOUND_HINT =
  "When a check first reported this problem. Later checks that report it again do not change this date.";

export const DAYS_OPEN_HINT =
  "How long ago a check first reported this problem, in whole days.";

export const DECIDED_HINT =
  "When the latest approve or reject decision was recorded. Blank if nobody has decided.";

// ---- Certainty ------------------------------------------------------------

export const CERTAINTY_HINT: Record<string, string> = {
  confirmed:
    "The records agree on what happened and on which seller it affects.",
  suspected:
    "The link between the records was inferred rather than stated, so this needs a human check.",
};

// ---- How strongly the finding is tied to its records ----------------------

export const BASIS_COPY: Record<string, string> = {
  provider_reported: "Backed by PayPal's own record",
  derived_linkage: "Linked by inference, not stated by PayPal",
  amount_within_order: "Matched by amount within the order",
};
export const basisText = (basis: string): string =>
  BASIS_COPY[basis] ?? basis.replace(/_/g, " ");

// ---- Priority ---------------------------------------------------------------

export const PRIORITY_HINT =
  "How urgent this is. It combines the size of the difference, how many sellers are affected, how long it has been open, and how sure the AI is. Higher means look at it first.";

export const FACTOR_NAMES: Record<string, string> = {
  impact: "size of the difference",
  sellers: "sellers affected",
  age: "time open",
  confidence: "AI confidence",
};

// ---- Investigation ---------------------------------------------------------

export const VERDICT_TEXT: Record<string, string> = {
  reported: "Cause found",
  insufficient_evidence: "No clear cause",
  failed: "Investigation failed",
};

export const NO_CAUSE_EXPLAINED =
  "The AI looked through the records and could not find enough to name a cause, so it chose not to guess. That is a valid answer: check the records yourself.";

export const DECISION_EXPLAINED =
  "Approve means you agree with this finding and want it fixed. Reject means you disagree, or no action is needed. Either way this only records your decision and your name. It does not refund, adjust or pay anything.";

// ---- Tooltips used across screens ------------------------------------------

export const HINTS = {
  runNow:
    "Check every order against PayPal's records now. The same check also runs every night.",
  parked:
    "Not a mistake. One record has arrived and its match has not, so it is held back from the queue and checked again on the next run.",
  clean:
    "A completed check covered the order after it arrived, and found nothing wrong.",
  notExamined:
    "No completed check has looked at these since they arrived, so they are not claimed to be fine.",
  runFindings:
    "Everything the run concluded about an order, including items on hold.",
  runExceptions: "The findings that need a person. Items on hold are not counted.",
  runWindow: "The run checks orders placed within these dates.",
  investigated: "Exceptions the AI has looked into and reached an answer on.",
} as const;

// ---- Record fields (evidence viewer) --------------------------------------

const FIELD_LABELS: Record<string, string> = {
  "Paypal fee": "PayPal fee",
  "Gross amount": "Gross amount",
  "Net amount": "Net amount",
  "Settlement amount": "Settled amount",
  "Settlement currency": "Settlement currency",
  "Disbursement mode": "Payout timing",
  "Disbursement state": "Payout status",
  "Reconciliation state": "Matching status",
  "Entry category": "Ledger category",
  "Transaction type": "Direction",
  "Occurred at": "Date",
  "Placed at": "Order date",
  "Capture linkage is derived": "Link to the payment was inferred",
  "Capture linkage href": "Link to the payment",
  "Total refunded amount": "Total refunded so far",
};
export const fieldLabel = (label: string): string => FIELD_LABELS[label] ?? label;

const VALUE_WORDS: Record<string, string> = {
  instant: "Instant",
  delayed: "Delayed",
  ingested: "Received",
  matched: "Matched",
  awaiting_evidence: "Waiting for the other record",
  unmatched: "No match",
  credit: "Credit",
  debit: "Debit",
};
export const fieldValue = (value: string): string => {
  const key = value.toLowerCase();
  if (VALUE_WORDS[key]) return VALUE_WORDS[key];
  return /^[a-z]+(_[a-z]+)+$/.test(value) ? value.replace(/_/g, " ") : value;
};

export const sourceName = (source: string): string =>
  source === "paypal" ? "PayPal" : source === "marketplace" ? "the marketplace" : source;
