import { typeTitle } from "./copy";

// The queue, the drawer and the filters all name a type by its plain-language
// title; the raw code stays an internal identifier.
export const label = typeTitle;

export const formatDate = (iso: string): string =>
  new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(iso));

export const formatDateTime = (iso: string): string =>
  new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(iso));
