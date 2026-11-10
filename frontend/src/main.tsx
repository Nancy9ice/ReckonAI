import { ModuleRegistry } from "ag-grid-community";
import { AllEnterpriseModule, LicenseManager } from "ag-grid-enterprise";
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

// The licence key is a build-time variable. Without it the grid still works
// and shows AG Grid's evaluation watermark; nothing here fails.
const licenceKey = import.meta.env.VITE_AG_GRID_LICENSE_KEY;
if (licenceKey) LicenseManager.setLicenseKey(licenceKey);

ModuleRegistry.registerModules([AllEnterpriseModule]);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
