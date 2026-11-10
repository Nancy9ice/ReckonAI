import { themeQuartz } from "ag-grid-community";

// The palette: PayPal-adjacent navy and blue on a cool neutral. The tokens
// mirror styles.css so the grid and the page read as one surface.
export const gridTheme = themeQuartz.withParams({
  accentColor: "#0070BA",
  backgroundColor: "#FFFFFF",
  foregroundColor: "#2C2E2F",
  headerBackgroundColor: "#F5F7FA",
  headerTextColor: "#001C64",
  headerFontWeight: 600,
  borderColor: "#E3E8EF",
  rowHoverColor: "#F0F6FC",
  selectedRowBackgroundColor: "#E3F0FB",
  fontFamily: "inherit",
  fontSize: 13,
  spacing: 7,
  wrapperBorderRadius: 14,
  borderRadius: 8,
});
