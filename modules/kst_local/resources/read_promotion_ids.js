const crypto = require("crypto");

const identity = process.argv[2];
const databasePath = process.argv[3];
const sqliteModulePath = process.argv[4];
const Database = require(sqliteModulePath);

const sha256 = crypto.createHash("sha256").update(identity).digest("hex");
const key = crypto.createHash("md5").update(sha256).digest("hex");
const db = new Database(databasePath, { readonly: true, fileMustExist: true });

function quoteIdentifier(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function extractPromotionIds(value) {
  const text = String(value || "");
  const ids = [];
  const patterns = [
    /(?:百度)?推广\s*ID\s*[:："'\\\s]*(\d{5,})/gi,
    /"promotionId"\s*:\s*"?(\d{5,})"?/gi,
  ];
  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      ids.push(match[1]);
    }
  }
  return ids;
}

try {
  db.pragma("cipher='sqlcipher'");
  db.pragma("legacy=4");
  db.pragma(`key='${key}'`);

  const tables = db
    .prepare("select name from sqlite_master where type = 'table' order by name")
    .all();
  const promotionIds = new Set();
  for (const table of tables.filter((item) => /visitor/i.test(item.name))) {
    const tableName = quoteIdentifier(table.name);
    const columns = new Set(
      db
        .prepare(`pragma table_info(${tableName})`)
        .all()
        .map((item) => item.name),
    );
    const selected = ["visitorCustomField", "info"].filter((name) =>
      columns.has(name),
    );
    if (selected.length === 0) {
      continue;
    }
    const sql = `select ${selected
      .map(quoteIdentifier)
      .join(", ")} from ${tableName}`;
    for (const row of db.prepare(sql).iterate()) {
      for (const column of selected) {
        for (const promotionId of extractPromotionIds(row[column])) {
          promotionIds.add(promotionId);
        }
      }
    }
  }
  process.stdout.write(
    JSON.stringify({ promotionIds: Array.from(promotionIds).sort() }),
  );
} finally {
  db.close();
}
