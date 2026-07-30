/// Chapter 7: Mobile-First Architecture for iGaming
/// Flutter BLoC Architecture
///
/// Flutter implementation of the gambling app using the BLoC pattern with:
/// - GameEvent hierarchy (LoadGame, PlaceBet, UpdateConnectionStatus)
/// - GameState with offline/online mode flags and bet list
/// - GameBloc with Hive cache for offline game state storage
/// - Optimistic bet placement with automatic rollback on failure
/// - Connectivity monitoring and queued operation sync on reconnect
///
/// Reference: Chapter 7 - Flutter Alternative Architecture section

import 'package:flutter_bloc/flutter_bloc.dart';
import 'package:equatable/equatable.dart';
import 'package:hive/hive.dart';
import 'package:connectivity_plus/connectivity_plus.dart';

// Events
abstract class GameEvent extends Equatable {
  const GameEvent();

  @override
  List<Object> get props => [];
}

class LoadGame extends GameEvent {
  final String gameId;
  const LoadGame(this.gameId);
}

class PlaceBet extends GameEvent {
  final double amount;
  final Map<String, dynamic> betData;
  const PlaceBet(this.amount, this.betData);
}

class UpdateConnectionStatus extends GameEvent {
  final ConnectivityResult status;
  const UpdateConnectionStatus(this.status);
}

// States
class GameState extends Equatable {
  final String? currentGameId;
  final double balance;
  final List<Bet> bets;
  final ConnectionStatus connectionStatus;
  final bool isOffline;

  const GameState({
    this.currentGameId,
    this.balance = 0.0,
    this.bets = const [],
    this.connectionStatus = ConnectionStatus.online,
    this.isOffline = false,
  });

  GameState copyWith({
    String? currentGameId,
    double? balance,
    List<Bet>? bets,
    ConnectionStatus? connectionStatus,
    bool? isOffline,
  }) {
    return GameState(
      currentGameId: currentGameId ?? this.currentGameId,
      balance: balance ?? this.balance,
      bets: bets ?? this.bets,
      connectionStatus: connectionStatus ?? this.connectionStatus,
      isOffline: isOffline ?? this.isOffline,
    );
  }

  @override
  List<Object?> get props => [
    currentGameId,
    balance,
    bets,
    connectionStatus,
    isOffline
  ];
}

// BLoC with Offline Support
class GameBloc extends Bloc<GameEvent, GameState> {
  final GameRepository repository;
  final Connectivity connectivity;
  final Box<GameState> cacheBox;

  GameBloc({
    required this.repository,
    required this.connectivity,
    required this.cacheBox,
  }) : super(const GameState()) {
    on<LoadGame>(_onLoadGame);
    on<PlaceBet>(_onPlaceBet);
    on<UpdateConnectionStatus>(_onUpdateConnectionStatus);

    // Monitor connectivity changes
    connectivity.onConnectivityChanged.listen((result) {
      add(UpdateConnectionStatus(result));
    });
  }

  Future<void> _onLoadGame(LoadGame event, Emitter<GameState> emit) async {
    try {
      // Check cache first for offline support
      if (state.isOffline) {
        final cachedState = cacheBox.get(event.gameId);
        if (cachedState != null) {
          emit(cachedState);
          return;
        }
      }

      // Load from API
      final gameState = await repository.getGameState(event.gameId);

      // Cache for offline access
      await cacheBox.put(event.gameId, gameState);

      emit(state.copyWith(
        currentGameId: event.gameId,
        balance: gameState.balance,
      ));
    } catch (e) {
      // Handle errors gracefully
      emit(state.copyWith(
        connectionStatus: ConnectionStatus.error,
      ));
    }
  }

  Future<void> _onPlaceBet(PlaceBet event, Emitter<GameState> emit) async {
    if (state.balance < event.amount) {
      emit(state.copyWith(
        connectionStatus: ConnectionStatus.insufficientFunds,
      ));
      return;
    }

    // Optimistic update
    final newBalance = state.balance - event.amount;
    final newBet = Bet(
      id: generateBetId(),
      amount: event.amount,
      timestamp: Date.now(),
      status: BetStatus.pending,
    );

    emit(state.copyWith(
      balance: newBalance,
      bets: [...state.bets, newBet],
    ));

    try {
      if (state.isOffline) {
        // Queue for later sync
        await _queueBetForSync(newBet);
      } else {
        // Place bet online
        final result = await repository.placeBet(
          state.currentGameId!,
          event.amount,
          event.betData,
        );

        // Update with server response
        final updatedBets = state.bets.map((bet) =>
          bet.id == newBet.id ? result : bet
        ).toList();

        emit(state.copyWith(
          bets: updatedBets,
          balance: result.newBalance,
        ));
      }
    } catch (e) {
      // Revert on failure
      emit(state.copyWith(
        balance: state.balance + event.amount,
        bets: state.bets.where((bet) => bet.id != newBet.id).toList(),
        connectionStatus: ConnectionStatus.error,
      ));
    }
  }

  void _onUpdateConnectionStatus(
    UpdateConnectionStatus event,
    Emitter<GameState> emit
  ) {
    final isOffline = event.status == ConnectivityResult.none;
    emit(state.copyWith(
      connectionStatus: _mapConnectivityToStatus(event.status),
      isOffline: isOffline,
    ));

    if (!isOffline) {
      // Sync queued operations when coming back online
      _syncQueuedOperations();
    }
  }
}
