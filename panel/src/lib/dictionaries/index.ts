// Merges the shared "chrome" dictionary with per-page dictionaries into the two
// full Dict objects the i18n provider uses. Add a new page's import + spread here
// once its dictionaries/<page>.ts file exists.
import { commonEn, commonTr } from "./common"
import { sharingEn, sharingTr } from "./sharing"
import { clientsEn, clientsTr } from "./clients"
import { videographerEn, videographerTr } from "./videographer"
import { planningEn, planningTr } from "./planning"
import { mailAdminEn, mailAdminTr } from "./mail-admin"
import { mediaToolsEn, mediaToolsTr } from "./media-tools"

export type Dict = Record<string, string>

export const tr: Dict = {
  ...commonTr,
  ...sharingTr,
  ...clientsTr,
  ...videographerTr,
  ...planningTr,
  ...mailAdminTr,
  ...mediaToolsTr,
}

export const en: Dict = {
  ...commonEn,
  ...sharingEn,
  ...clientsEn,
  ...videographerEn,
  ...planningEn,
  ...mailAdminEn,
  ...mediaToolsEn,
}
