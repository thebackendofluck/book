// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Workflow Engine
// Manages complaints, disputes, and escalation workflows with state machine transitions

import * as express from 'express';
import { workFlows } from '../../../shared/workflow/complaints';
import { WorkflowItem } from '../../../shared/workflow/models/workflow-item';
import { WorkflowItemHistory } from '../../../shared/workflow/models/workflow-item-history';
import { WorkflowState } from '../../../shared/workflow/models/workflow-state';
import { Workflow } from '../../../shared/workflow/models/workflow';
import { Database } from '../database';
import { AuthenticatedResponse } from '../models/AuthenticatedResponse';
import * as xml2js from 'xml2js';

export const workFlowRouter = express.Router();

const allWorkFlows: Workflow[] = [];
for (let wf of workFlows) {
  allWorkFlows.push(wf);
}

// Persist workflow item history entries (new entries only)
async function updateItemHistory(
  item: WorkflowItem,
  runQuery: (
    namespace: string,
    scriptName: string,
    params: { [key: string]: any }
  ) => Promise<any>
) {
  for (var h of item.itemHistory) {
    if (!h.id) {
      h.id = (await runQuery('workflow', 'getNewItemHistoryId', {}))[0].NEXT;

      await runQuery('workflow', 'createItemHistory', {
        id: h.id,
        workflow_item_id: item.id,
        created_date: new Date(
          (h.date instanceof Date ? h.date : new Date(h.date)).toUTCString()
        ),
        item_comment: h.comment,
        username: h.user,
        state_id: h.stateId,
      });
    }
  }
}

// Retrieve a single workflow item with XML field data and history
async function getItem(db: Database, id: number, brands: string) {
  let item = await db.runQuery('workflow', 'getItem', { id: id, brands: brands });

  if (item && item.length === 1) {
    let dbItem = item[0];

    let pItem = new WorkflowItem({
      fieldData: null,
      id: dbItem.ID,
      currentStateId: dbItem.CURRENT_STATE_ID,
      workflowId: dbItem.WORKFLOW_ID,
      playerId: dbItem.GAME_USER_ID,
      playerUsername: dbItem.USERNAME,
    });

    // Parse XML field data stored in Oracle CLOB
    let xmlParser = new xml2js.Parser({ explicitRoot: true, explicitArray: false });
    xmlParser.parseString(dbItem.FIELD_DATA, function (err, result) {
      pItem.setFieldDataParsed(result ? result.root ? result.root : result : null);
    });

    // Load history entries
    let dbHistory = await db.runQuery('workflow', 'getItemHistory', { id: id });
    pItem.itemHistory = [];

    for (let h of dbHistory) {
      pItem.itemHistory.push(
        new WorkflowItemHistory({
          id: h.ID,
          workflowItemId: h.WORKFLOW_ITEM_ID,
          date: h.CREATED,
          comment: h.ITEM_COMMENT,
          user: h.USERNAME,
          stateId: h.STATE_ID,
        })
      );
    }

    return pItem;
  }

  return null;
}

// Create or update a workflow item within a transaction
async function createNewItem(
  db: Database,
  data: WorkflowItem,
  user: string,
  brandsCsv: string
) {
  let newItem = new WorkflowItem(data);
  let xmlBuilder = new xml2js.Builder();

  if (newItem.id === 0) {
    // New item: generate ID, link to player, create history
    newItem.id = (await db.runQuery('workflow', 'getNewId', {}))[0].NEXT;
    newItem.itemHistory.forEach((h) => (h.user = user));

    let dbData = {
      id: newItem.id,
      workflow_id: newItem.workflowId,
      current_state_id: newItem.currentStateId,
      field_data: newItem.fieldData
        ? xmlBuilder.buildObject(newItem.getFieldDataParsed())
        : '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><root />',
    };

    await db.runMultipleQueriesInTransaction(async (runQuery) => {
      await runQuery('workflow', 'createItem', dbData);
      if (newItem.playerId) {
        await runQuery('workflow', 'setItemGameUser', {
          id: newItem.id,
          userid: newItem.playerId,
        });
      }
      await updateItemHistory(newItem, runQuery);
    });

    return newItem;
  } else {
    // Existing item: merge field changes, update history
    let item = await getItem(db, data.id, brandsCsv);
    let workflow = allWorkFlows.find((w) => w.id === item.workflowId);

    if (item) {
      newItem.itemHistory = item.itemHistory;
      item.updateFieldData(newItem.fieldData, user, workflow.fields);

      let dbData = {
        id: item.id,
        workflow_id: item.workflowId,
        current_state_id: newItem.currentStateId,
        field_data: item.fieldData
          ? xmlBuilder.buildObject(item.getFieldDataParsed())
          : '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><root />',
      };

      await db.runMultipleQueriesInTransaction(async (runQuery) => {
        await runQuery('workflow', 'updateItem', dbData);
        await updateItemHistory(item, runQuery);
      });

      return item;
    }
    return null;
  }
}

// State machine: validate and execute state transitions with pre/post hooks
async function updateItemState(
  db: Database,
  id: number,
  state: number,
  comment: string,
  user: string,
  brandsCsv: string
) {
  let item = await getItem(db, id, brandsCsv);

  if (item) {
    let messages = [];
    let workflow = workFlows.find((w) => w.id === item.workflowId);

    // Validate state transition is allowed
    let stateChange = workflow.stateChanges.find(
      (s) => s.fromStateId === item.currentStateId && s.toStateId === state
    );

    if (!stateChange && item.currentStateId !== state) {
      return 0; // Invalid transition
    }

    // Pre-change hook (e.g., validation)
    if (stateChange && stateChange.preChangeEvent) {
      await stateChange.preChangeEvent(item, {});
    }

    item.setState(state, user, comment);

    let xmlBuilder = new xml2js.Builder();
    let dbData = {
      id: item.id,
      workflow_id: item.workflowId,
      current_state_id: item.currentStateId,
      field_data: item.fieldData
        ? xmlBuilder.buildObject(item.getFieldDataParsed())
        : '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><root />',
    };

    db.runMultipleQueriesInTransaction(async (runQuery) => {
      await db.runQuery('workflow', 'updateItem', dbData);
      await updateItemHistory(item, runQuery);
    });

    // Post-change hook (e.g., create linked workflow items)
    if (stateChange && stateChange.postChangeEvent) {
      let resultMessage = await stateChange.postChangeEvent(item, {
        createNewWorkflowItem: async (newItemData) => {
          return await createNewItem(db, newItemData, user, brandsCsv);
        },
        runStateChange: async (id, state, comment) => {
          return await updateItemState(db, id, state, comment, user, brandsCsv);
        },
        currentUser: user,
      });

      if (resultMessage) {
        messages.push(resultMessage);
      }
    }

    messages.unshift('State updated successfully');

    return { item: item, messages: messages };
  }
  return null;
}

// --- Express Routes ---

workFlowRouter
  // UKGC complaints return report (regulatory requirement)
  .get('/ukgc-complaints-return', async function (req, res: AuthenticatedResponse) {
    let from = new Date(new Date(req.query.date_from).toUTCString());
    let to = new Date(new Date(req.query.date_to).toUTCString());
    let db = res.locals.database;

    let data = await db.runQuery('workflow', 'ukgc-complaints-return', {
      datefrom: from,
      dateto: to,
    });
    res.json(data[0]);
  })

  // Get workflow definitions by type
  .get('/:typeId(\\d+)', function (req, res: AuthenticatedResponse) {
    let id = req.params.typeId;
    let workFlows = allWorkFlows.filter((w) => w.typeId == id);

    if (workFlows) {
      res.json(workFlows);
    } else {
      res.status(404);
      res.json({ message: 'workflow type not found' });
    }
  })

  // Create or update workflow item
  .post('/item', async function (req, res: AuthenticatedResponse) {
    let item = await createNewItem(
      res.locals.database,
      req.body,
      res.locals.user.firstName + ' ' + res.locals.user.lastName,
      res.locals.user.brandCsv
    );

    if (item == null) {
      res.status(404);
      res.json({ message: 'workflow item not found' });
    } else {
      res.json(item);
    }
  })

  // Update workflow item state (state machine transition)
  .post('/item/state', async function (req, res: AuthenticatedResponse) {
    let result = await updateItemState(
      res.locals.database,
      req.body.id,
      req.body.state,
      req.body.comment,
      res.locals.user.firstName + ' ' + res.locals.user.lastName,
      res.locals.user.brandCsv
    );

    if (result === 0) {
      res.status(409);
      res.json({ message: 'invalid status change' });
      return;
    }

    if (result == null) {
      res.status(404);
      res.json({ message: 'workflow item not found' });
      return;
    }

    res.json(result);
  })

  // Get single workflow item
  .get('/item/:id', async function (req, res: AuthenticatedResponse) {
    let pItem = await getItem(
      res.locals.database,
      req.params.id,
      res.locals.user.brandCsv
    );

    if (pItem) {
      res.json(pItem);
    } else {
      res.status(404);
      res.json({ message: 'workflow item not found' });
    }
  })

  // List workflow items with filtering (brand-scoped)
  .get('/items', async function (req, res: AuthenticatedResponse) {
    let db = res.locals.database;
    let states: WorkflowState[] = [];

    for (let l of workFlows) {
      for (let s of l.states) {
        states.push(s);
      }
    }

    let dbItems = await db.runQuery('workflow', 'list', {
      workflow_ids: req.query.workflow_ids,
      current_state: req.query.show_only_open
        ? states
            .filter((s) => !s.closed)
            .map((s) => s.id)
            .join(',')
        : states.map((s) => s.id).join(','),
      game_user_id: req.query.game_user_id,
      usersearch: req.query.usersearch,
      id: req.query.id,
      ref: req.query.ref,
      brands: res.locals.user.brandCsv,
    });

    let parsedItems: WorkflowItem[] = [];
    let xmlParser = new xml2js.Parser();

    for (let dbItem of dbItems) {
      let item = new WorkflowItem({
        fieldData: null,
        id: dbItem.ID,
        currentStateId: dbItem.CURRENT_STATE_ID,
        workflowId: dbItem.WORKFLOW_ID,
        playerId: dbItem.GAME_USER_ID,
        playerUsername: dbItem.USERNAME,
        brandName: dbItem.BRAND,
        created: dbItem.CREATED,
      });

      xmlParser.parseString(dbItem.FIELD_DATA, function (err, result) {
        item.setFieldDataParsed(result ? result.root ? result.root : result : null);
      });

      parsedItems.push(item);
    }

    res.json({ items: parsedItems, workflows: workFlows });
  });

export default workFlowRouter;
