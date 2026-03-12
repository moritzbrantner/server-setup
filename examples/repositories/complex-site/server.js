const express = require("express");
const path = require("path");

const app = express();
const port = Number(process.env.PORT || 3000);
const apiBaseUrl = process.env.API_BASE_URL || "http://api.localhost:8080";

app.use("/assets", express.static(path.join(__dirname, "public", "assets")));

app.get("/healthz", (_req, res) => {
  res.json({ status: "ok", apiBaseUrl, port });
});

app.get("/config.js", (_req, res) => {
  res.type("application/javascript");
  res.send(`window.APP_CONFIG = ${JSON.stringify({ apiBaseUrl })};`);
});

app.get("/", (_req, res) => {
  res.sendFile(path.join(__dirname, "views", "index.html"));
});

app.listen(port, "0.0.0.0", () => {
  console.log(`complex-site listening on ${port}`);
});
