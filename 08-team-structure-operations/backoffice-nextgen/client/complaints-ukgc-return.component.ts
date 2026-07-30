// Companion code for "The Backend of Luck" - Chapter 08, Team Structure and Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Backoffice - UKGC Complaints Return Report
// Generates regulatory complaints data for UK Gambling Commission returns

import { Component, OnInit } from '@angular/core';
import { WorkflowService } from '../../../services/workflow.service';
import { Observable } from 'rxjs/Observable';
import { Subject } from 'rxjs/Subject';

import 'rxjs/add/observable/of';
import 'rxjs/add/operator/catch';
import 'rxjs/add/operator/debounceTime';
import 'rxjs/add/operator/distinctUntilChanged';
import 'rxjs/add/operator/switchMap';

@Component({
  selector: 'app-complaints-ukgc-return',
  templateUrl: './complaints-ukgc-return.component.html',
  styleUrls: ['./complaints-ukgc-return.component.css'],
  providers: [WorkflowService],
})
export class ComplaintsUkGCReturnComponent implements OnInit {
  private data: Observable<any>;
  private searchTerms = new Subject<any>();
  public dateFrom: string;
  dateTo: string;
  public isLoading: boolean = false;
  public results: { name: string; value: number }[];

  constructor(private _workflowService: WorkflowService) {}

  ngOnInit() {
    this.data = this.searchTerms
      .debounceTime(300)
      .switchMap((term) => {
        this.isLoading = true;
        return this._workflowService.getUKGCReturnResults(term.dateFrom, term.dateTo);
      })
      .catch((err) => {
        this.isLoading = false;
        return null;
      });

    this.data.subscribe(
      (data) => {
        this.results = [];

        // Transform API response object into display-ready array
        for (var n of Object.getOwnPropertyNames(data)) {
          this.results.push({ name: this.toTitleCase(n.toLowerCase()), value: data[n] });
        }
        this.isLoading = false;
      },
      (error) => {}
    );
  }

  run() {
    this.searchTerms.next({
      dateFrom: new Date(this.dateFrom),
      dateTo: new Date(this.dateTo),
    });
  }

  // Convert field names to title case for display
  // Handles gambling-specific abbreviations (GB, ROW, ADR)
  toTitleCase = (str) => {
    const articles = ['a', 'an', 'the'];
    const conjunctions = ['for', 'and', 'nor', 'but', 'or', 'yet', 'so'];
    const prepositions = [
      'with', 'at', 'from', 'into', 'upon', 'of', 'to', 'in', 'for',
      'on', 'by', 'like', 'over', 'plus', 'but', 'up', 'down', 'off', 'near',
    ];

    const replaceCharsWithSpace = (str) =>
      str.replace(/[^0-9a-z&/\\]/gi, ' ').replace(/(\s\s+)/gi, ' ');
    const capitalizeFirstLetter = (str) => str.charAt(0).toUpperCase() + str.substr(1);
    const normalizeStr = (str) => str.toLowerCase().trim();
    const shouldCapitalize = (word, fullWordList, posWithinStr) => {
      if (posWithinStr === 0 || posWithinStr === fullWordList.length - 1) return true;
      return !(
        articles.includes(word) ||
        conjunctions.includes(word) ||
        prepositions.includes(word)
      );
    };

    str = replaceCharsWithSpace(str);
    str = normalizeStr(str);
    let words = str.split(' ');

    if (words.length <= 2) {
      words = words.map((w) => capitalizeFirstLetter(w));
    } else {
      for (let i = 0; i < words.length; i++) {
        words[i] = shouldCapitalize(words[i], words, i)
          ? (<any>capitalizeFirstLetter)(words[i], words, i)
          : words[i];
      }
    }

    // Uppercase gambling-specific abbreviations
    for (let upper of ['gb', 'row', 'adr']) {
      words = words.map((w) => (w.toLowerCase() === upper ? w.toUpperCase() : w));
    }

    return words.join(' ');
  };
}
