// 门店数据（Excel）文件：知识库上传和门店数据导入共用
export const STORE_DATA_ACCEPT = ".xlsx,.xlsm,.xls";
export const isStoreDataFile = (file) => /\.(xlsx|xlsm|xls)$/i.test(file.name);
