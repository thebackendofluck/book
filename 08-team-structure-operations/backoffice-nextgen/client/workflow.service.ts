// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Workflow HTTP Service
// Angular service for workflow API communication

import { HttpClient, HttpResponse, HttpParams } from '@angular/common/http';
import { Http, Response } from '@angular/http';
import { Injectable } from '@angular/core';
import { environment } from './../environments/environment';

import { Observable } from 'rxjs/Observable';
import 'rxjs/add/observable/of';
import 'rxjs/add/observable/from';
import 'rxjs/add/operator/do';
import 'rxjs/add/operator/delay';
import 'rxjs/add/operator/catch';
import 'rxjs/add/observable/throw';
import 'rxjs/add/operator/map';
import 'rxjs/BehaviorSubject';

import { Workflow } from '../../../shared/workflow/models/workflow';
import { WorkflowItem } from '../../../shared/workflow/models/workflow-item';

@Injectable()
export class WorkflowService {
  constructor(private http: HttpClient) {}

  // Get workflow definitions by type ID
  public getWorkflowByTypeId(workflowTypeId: number): Observable<Workflow[]> {
    return this.http
      .get(environment.apiBaseUrl + 'workflow/' + workflowTypeId)
      .map((data: any[]) => {
        let returnData = [];
        for (let item of data) {
          returnData.push(new Workflow(item));
        }
        return returnData;
      });
  }

  // Get single workflow item by ID
  public getWorkflowItem(workflowItemId: number): Observable<WorkflowItem> {
    return this.http
      .get(environment.apiBaseUrl + 'workflow/item/' + workflowItemId)
      .map((data: any) => new WorkflowItem(data));
  }

  // UKGC complaints return -- regulatory reporting endpoint
  public getUKGCReturnResults(date_from: Date, date_to: Date) {
    let params = new HttpParams();
    params = params.set('date_from', date_from.toISOString());
    params = params.set('date_to', date_to.toISOString());

    return this.http.get(environment.apiBaseUrl + 'workflow/ukgc-complaints-return', {
      params: params,
    });
  }

  // Search workflow items with filters
  public getWorkflowItems(
    searchOptions: { [id: string]: string }
  ): Observable<{ items: WorkflowItem[]; workflows: Workflow[] }> {
    let params = new HttpParams();

    for (let k of Object.keys(searchOptions)) {
      if (searchOptions[k]) {
        params = params.set(k, searchOptions[k]);
      }
    }

    return this.http
      .get(environment.apiBaseUrl + 'workflow/items', { params: params })
      .map((serverData: any) => {
        let items = [];
        let workflows = [];

        for (let item of serverData.items) {
          items.push(new WorkflowItem(item));
        }
        for (let wf of serverData.workflows) {
          workflows.push(new Workflow(wf));
        }

        return { items: items, workflows: workflows };
      });
  }

  // Create or update a workflow item
  public saveWorkflowItem(item: WorkflowItem): Observable<WorkflowItem> {
    return this.http
      .post(environment.apiBaseUrl + 'workflow/item', item)
      .map((data) => new WorkflowItem(data));
  }

  // Execute a state transition on a workflow item
  public updateWorkflowState(
    workflowItemId: number,
    newStateId: number,
    comment: string
  ): Observable<{ item: WorkflowItem; messages: string[] }> {
    return this.http
      .post(environment.apiBaseUrl + 'workflow/item/state', {
        id: workflowItemId,
        state: newStateId,
        comment: comment,
      })
      .map((data) => ({
        item: new WorkflowItem(data['item']),
        messages: data['messages'],
      }));
  }

  private handleError(error: Response | any) {
    let errMsg: string;

    if (error instanceof Response) {
      try {
        const body = error.json() || '';
        const err = body.error || JSON.stringify(body);
        errMsg = `${error.status} - ${error.statusText || ''} ${err}`;
      } catch (e) {
        errMsg = error.status + ' - ' + error.statusText;
      }
    } else {
      errMsg = error.message ? error.message : error.toString();
    }

    console.error(error, errMsg);
    return Observable.throw(errMsg);
  }
}
