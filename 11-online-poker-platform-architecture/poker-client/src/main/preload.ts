// Companion code for "The Backend of Luck" - Chapter 11, Online Poker Platform Architecture.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("pokerApi", {
  getConfig: () => ipcRenderer.invoke("get-config"),
  connect: (url: string) => ipcRenderer.invoke("poker:connect", url),
});
