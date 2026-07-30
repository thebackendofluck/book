// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - Complaints List Component
// Angular component with reactive search using RxJS debounce

import { Component, OnInit } from '@angular/core';
import { WorkflowItem } from '../../../../../shared/workflow/models/workflow-item';
import { Workflow } from '../../../../../shared/workflow/models/workflow';
import { WorkflowService } from '../../../services/workflow.service';
import { Observable } from 'rxjs/Observable';
import { Subject } from 'rxjs/Subject';

import 'rxjs/add/observable/of';
import 'rxjs/add/operator/catch';
import 'rxjs/add/operator/debounceTime';
import 'rxjs/add/operator/distinctUntilChanged';
import 'rxjs/add/operator/switchMap';

interface ComplaintsSearchFields {
  workflow_ids;
  show_only_open;
  game_user_id;
  usersearch;
  id;
}

@Component({
  selector: 'app-complaints',
  templateUrl: './complaints.component.html',
  styleUrls: ['./complaints.component.css'],
  providers: [WorkflowService],
})
export class ComplaintsComponent implements OnInit {
  public id: string;
  public ref: string;
  public user: string;
  public type: number = 0;
  public status: number = 0;
  public isLoading: boolean = true;

  constructor(private _workflowService: WorkflowService) {}

  ngOnInit() {
    // Reactive search: debounce user input, cancel previous requests
    this.data = this.searchTerms
      .debounceTime(300)
      .switchMap((term) => {
        this.items = [];
        this.workflows = [];
        this.isLoading = true;
        return this._workflowService.getWorkflowItems(term);
      })
      .catch((error) => {
        this.isLoading = false;
        console.log(`Error in component ... ${error}`);
        return Observable.of<{ items: WorkflowItem[]; workflows: Workflow[] }>({
          items: [],
          workflows: [],
        });
      });

    this.data.subscribe(
      (data) => {
        this.items = data.items;
        this.workflows = data.workflows;
        this.isLoading = false;
      },
      (error) => {}
    );

    // Trigger initial search on component load
    this.search();
  }

  public search() {
    let terms = {
      id: this.id ? +this.id : undefined,
      workflow_ids: undefined,
      show_only_open: undefined,
      game_user_id: undefined,
      usersearch: undefined,
      ref: this.ref ? this.ref : undefined,
    };

    if (this.user) {
      let uid = parseInt(this.user);
      if (!isNaN(uid)) {
        terms.game_user_id = +uid;
      } else {
        terms.usersearch = this.user;
      }
    }

    // Workflow types: 1=Complaints, 2=Disputes, 3=Internal Escalations
    let workflows = [1, 2, 3];
    if (this.type > 0) {
      workflows = [this.type];
    }
    terms.workflow_ids = workflows.join(',');
    terms.show_only_open = this.status === 0 ? 1 : 0;

    this.searchTerms.next(terms);
  }

  items: WorkflowItem[];
  private data: Observable<any>;
  private searchTerms = new Subject<any>();
  workflows: Workflow[];

  public newItem() {
    this.selectedWorkflowItemId = this.selectedWorkflowItemId === 0 ? -1 : 0;
    this.showDetailsDialog = true;
  }

  public selectedBrands;
  public brands = [{ name: 'All Brands' }];

  // Resolve current state name from workflow definition
  public getCurrentStateName(item: WorkflowItem) {
    let wf = this.workflows.find((w) => w.id === item.workflowId);
    return wf ? wf.states.find((s) => s.id === item.currentStateId).title : '';
  }

  public selectItem(itemId) {
    this.selectedWorkflowItemId = itemId;
    this.showDetailsDialog = true;
  }

  hideDialog() {
    this.search();
    this.showDetailsDialog = false;
    return false;
  }

  showDetailsDialog: boolean = false;
  selectedWorkflowItemId: number;
}
