/**
 * Google Apps Script for locking a Google Sheets column on a schedule.
 *
 * Usage:
 * 1. Open the target Google Sheet.
 * 2. Go to Extensions > Apps Script.
 * 3. Paste this file into the Apps Script editor.
 * 4. Update CONFIG below.
 * 5. Run installDailyLockTrigger or installOneTimeLockTrigger once.
 *
 * The trigger runs as the Google account that installs it. That account must
 * have permission to protect ranges in the target spreadsheet.
 */
const CONFIG = {
  // Leave blank when this script is bound to the target spreadsheet.
  spreadsheetId: '',

  sheetName: 'Sheet1',

  // Column to lock. Use either a letter, such as 'C', or a 1-based number.
  column: 'C',

  // Rows above this number stay editable. Use 1 to include the header row.
  startRow: 2,

  // Users who should still be allowed to edit the protected column.
  // Leave empty to allow only the spreadsheet owner and script runner.
  allowedEditors: [
    // 'manager@example.com',
  ],

  // Makes the column visually stay in place while scrolling, in addition to
  // locking edits. Set to false if you only want edit protection.
  freezePaneToo: true,

  protectionDescription: 'Scheduled column lock',

  // Daily trigger time. Apps Script uses the script project's timezone.
  triggerHour: 17,
  triggerMinute: 0,

  // One-time trigger date/time, used only by installOneTimeLockTrigger.
  oneTimeTrigger: {
    year: 2026,
    month: 6,
    day: 11,
    hour: 17,
    minute: 0,
  },
};

function lockColumnAtScheduledTime() {
  const spreadsheet = getSpreadsheet_();
  const sheet = spreadsheet.getSheetByName(CONFIG.sheetName);

  if (!sheet) {
    throw new Error(`Sheet not found: ${CONFIG.sheetName}`);
  }

  const column = getColumnNumber_(CONFIG.column);
  const startRow = Math.max(1, CONFIG.startRow);
  const lastRow = sheet.getMaxRows();
  const rowCount = Math.max(1, lastRow - startRow + 1);
  const range = sheet.getRange(startRow, column, rowCount, 1);

  removeExistingColumnProtections_(sheet, column, startRow);

  const protection = range.protect().setDescription(CONFIG.protectionDescription);
  protection.setWarningOnly(false);

  if (protection.canDomainEdit()) {
    protection.setDomainEdit(false);
  }

  const editors = protection.getEditors();
  if (editors.length > 0) {
    protection.removeEditors(editors);
  }

  if (CONFIG.allowedEditors.length > 0) {
    protection.addEditors(CONFIG.allowedEditors);
  }

  if (CONFIG.freezePaneToo) {
    sheet.setFrozenColumns(Math.max(sheet.getFrozenColumns(), column));
  }
}

function installDailyLockTrigger() {
  removeLockTriggers();

  ScriptApp.newTrigger('lockColumnAtScheduledTime')
    .timeBased()
    .everyDays(1)
    .atHour(CONFIG.triggerHour)
    .nearMinute(CONFIG.triggerMinute)
    .create();
}

function installOneTimeLockTrigger() {
  removeLockTriggers();

  const trigger = CONFIG.oneTimeTrigger;
  const date = new Date(
    trigger.year,
    trigger.month - 1,
    trigger.day,
    trigger.hour,
    trigger.minute,
    0
  );

  ScriptApp.newTrigger('lockColumnAtScheduledTime')
    .timeBased()
    .at(date)
    .create();
}

function removeLockTriggers() {
  ScriptApp.getProjectTriggers()
    .filter((trigger) => trigger.getHandlerFunction() === 'lockColumnAtScheduledTime')
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
}

function getSpreadsheet_() {
  if (CONFIG.spreadsheetId) {
    return SpreadsheetApp.openById(CONFIG.spreadsheetId);
  }

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  if (!spreadsheet) {
    throw new Error('No active spreadsheet. Set CONFIG.spreadsheetId for standalone scripts.');
  }
  return spreadsheet;
}

function getColumnNumber_(column) {
  if (typeof column === 'number') {
    return column;
  }

  const text = String(column).trim().toUpperCase();
  if (!/^[A-Z]+$/.test(text)) {
    throw new Error(`Invalid column: ${column}`);
  }

  return text
    .split('')
    .reduce((total, character) => total * 26 + character.charCodeAt(0) - 64, 0);
}

function removeExistingColumnProtections_(sheet, column, startRow) {
  sheet
    .getProtections(SpreadsheetApp.ProtectionType.RANGE)
    .filter((protection) => {
      const range = protection.getRange();
      return (
        protection.getDescription() === CONFIG.protectionDescription &&
        range.getColumn() === column &&
        range.getNumColumns() === 1 &&
        range.getRow() === startRow
      );
    })
    .forEach((protection) => protection.remove());
}
