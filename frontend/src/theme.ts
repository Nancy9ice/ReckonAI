import { themeQuartz } from "ag-grid-community";

// The palette: Violet on a cool neutral. The tokens
// mirror styles.css so the grid and the page read as one surface.
export const gridTheme = themeQuartz.withParams({
  accentColor: "#6C47FF",
  backgroundColor: "#FFFFFF",
  foregroundColor: "#241F3D",
  headerBackgroundColor: "#F7F5FD",
  headerTextColor: "#1D1547",
  headerFontWeight: 600,
  borderColor: "#E7E3F4",
  rowHoverColor: "#F4F0FF",
  selectedRowBackgroundColor: "#E9E2FF",
  fontFamily: "inherit",
  fontSize: 13,
  spacing: 7,
  wrapperBorderRadius: 14,
  borderRadius: 8,
});
