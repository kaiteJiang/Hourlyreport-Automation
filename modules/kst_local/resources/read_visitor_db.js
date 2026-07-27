const crypto = require("crypto");

const identity = process.argv[2];
const databasePath = process.argv[3];
const targetDate = process.argv[4];
const sqliteModulePath = process.argv[5];
const Database = require(sqliteModulePath);

const sha256 = crypto.createHash("sha256").update(identity).digest("hex");
const key = crypto.createHash("md5").update(sha256).digest("hex");
const db = new Database(databasePath, { readonly: true, fileMustExist: true });

function quoteIdentifier(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function selectColumn(columns, name, alias = name) {
  return columns.has(name)
    ? `${quoteIdentifier(name)} AS ${quoteIdentifier(alias)}`
    : `'' AS ${quoteIdentifier(alias)}`;
}

function expressionColumn(columns, names, alias) {
  const available = names.filter((name) => columns.has(name));
  if (available.length === 0) {
    return `'' AS ${quoteIdentifier(alias)}`;
  }
  const values = available.map(
    (name) => `nullif(${quoteIdentifier(name)}, '')`,
  );
  return `coalesce(${values.join(", ")}, '') AS ${quoteIdentifier(alias)}`;
}

function promotionId(value) {
  const match = String(value || "").match(
    /(?:百度)?推广\s*ID\s*[：:"\\\s]*(\d{5,})/i,
  );
  return match?.[1] || "";
}

try {
  db.pragma("cipher='sqlcipher'");
  db.pragma("legacy=4");
  db.pragma(`key='${key}'`);

  const tables = db
    .prepare("select name from sqlite_master where type = 'table' order by name")
    .all();
  const visitor = tables.find((item) => /visitor/i.test(item.name));
  if (!visitor) {
    throw new Error("visitor table not found");
  }
  const tableName = quoteIdentifier(visitor.name);
  const columns = new Set(
    db
      .prepare(`pragma table_info(${tableName})`)
      .all()
      .map((item) => item.name),
  );
  for (const required of ["recId", "vsSendNum", "visitorType", "channelType"]) {
    if (!columns.has(required)) {
      throw new Error(`required column missing: ${required}`);
    }
  }

  const startExpression = expressionColumn(
    columns,
    ["visitorFirstMessageTime", "dialogOpenTime", "curEnterTime"],
    "startTime",
  );
  const dateColumns = [
    "visitorFirstMessageTime",
    "dialogOpenTime",
    "curEnterTime",
  ].filter((name) => columns.has(name));
  if (dateColumns.length === 0) {
    throw new Error("conversation date columns missing");
  }
  const dateExpression = `substr(coalesce(${dateColumns
    .map((name) => `nullif(${quoteIdentifier(name)}, '')`)
    .join(", ")}), 1, 10)`;
  const sql = `
    select ${selectColumn(columns, "recId")},
           ${startExpression},
           ${selectColumn(columns, "vsSendNum", "visitorMessages")},
           ${selectColumn(columns, "visitorType")},
           ${selectColumn(columns, "channelType")},
           ${selectColumn(columns, "cusTypeTag", "tagIds")},
           ${selectColumn(columns, "visitorCustomField")},
           ${selectColumn(columns, "info")},
           ${expressionColumn(
             columns,
             ["keyword", "keywordDialog", "keywordAll"],
             "keyword",
           )},
           ${selectColumn(columns, "bidWord")}
      from ${tableName}
     where ${quoteIdentifier("vsSendNum")} > 0
       and ${dateExpression} = ?
     order by startTime, recId`;
  const safeRows = db
    .prepare(sql)
    .all(targetDate)
    .map((row) => ({
      recId: row.recId,
      startTime: row.startTime || "",
      visitorMessages: row.visitorMessages || 0,
      visitorType: row.visitorType || "",
      channelType: row.channelType,
      tagIds: row.tagIds || "",
      promotionId: promotionId(
        `${row.visitorCustomField || ""} ${row.info || ""}`,
      ),
      keyword: row.keyword || "",
      bidWord: row.bidWord || "",
    }));
  process.stdout.write(JSON.stringify({ safeRows }));
} finally {
  db.close();
}
