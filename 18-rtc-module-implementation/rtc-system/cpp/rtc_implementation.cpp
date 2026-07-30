// RTCIntegrationUE5/RTCManger.h
#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "WebSocketsModule.h"
#include "RTCTimestamp.h"
#include "RTCManger.generated.h"

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnTimestampReceived, const FRTCTimestamp&, Timestamp);
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnRTCError, const FString&, ErrorMessage);

UCLASS()
class RTCINTEGRATION_API ARTCManger : public AActor
{
    GENERATED_BODY()

public:
    ARTCManger();

    UFUNCTION(BlueprintCallable, Category = "RTC")
    void InitializeRTC(const FString& ServerUrl, const FString& ApiKey, const FString& SecretKey);

    UFUNCTION(BlueprintCallable, Category = "RTC")
    void GetTimestampAsync(const TMap<FString, FString>& Metadata);

    UFUNCTION(BlueprintCallable, Category = "RTC")
    void StartTimestampStreaming(int32 IntervalMs = 100);

    UFUNCTION(BlueprintCallable, Category = "RTC")
    void StopTimestampStreaming();

    UFUNCTION(BlueprintPure, Category = "RTC")
    bool IsConnected() const { return bIsConnected; }

    UPROPERTY(BlueprintAssignable, Category = "RTC")
    FOnTimestampReceived OnTimestampReceived;

    UPROPERTY(BlueprintAssignable, Category = "RTC")
    FOnRTCError OnError;

private:
    void SendHttpRequest(const FString& Endpoint, const FString& Method,
                        const TMap<FString, FString>& Headers,
                        const FString& Body = TEXT(""));

    void OnHttpResponseReceived(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bWasSuccessful);

    void ConnectWebSocket();
    void OnWebSocketConnected();
    void OnWebSocketMessage(const FString& Message);
    void OnWebSocketError(const FString& Error);

    bool VerifySignature(const FRTCTimestamp& Timestamp) const;

    FString GenerateHMAC(const FString& Data, const FString& Key) const;

    FString ServerUrl;
    FString ApiKey;
    FString SecretKey;
    bool bIsConnected;

    TSharedPtr<IWebSocket> WebSocket;
    FTimerHandle ReconnectTimerHandle;
};

// RTCTimestamp.h
#pragma once

#include "CoreMinimal.h"
#include "RTCTimestamp.generated.h"

USTRUCT(BlueprintType)
struct RTCINTEGRATION_API FRTCTimestamp
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    int64 Unix;

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    int64 Nano;

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    FString ISO8601;

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    FString Signature;

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    float Confidence;

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    float DriftMs;

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    FString Source;

    UPROPERTY(BlueprintReadOnly, Category = "RTC")
    TMap<FString, FString> Metadata;
};

// RTCManger.cpp
#include "RTCManger.h"
#include "HttpModule.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"
#include "WebSocketsModule.h"
#include "SHA256.h" // UE5 crypto utilities

ARTCManger::ARTCManger()
{
    PrimaryActorTick.bCanEverTick = false;
    bIsConnected = false;
}

void ARTCManger::InitializeRTC(const FString& InServerUrl, const FString& InApiKey,
                              const FString& InSecretKey)
{
    ServerUrl = InServerUrl;
    ApiKey = InApiKey;
    SecretKey = InSecretKey;

    UE_LOG(LogTemp, Log, TEXT("RTC Manager initialized with server: %s"), *ServerUrl);
}

void ARTCManger::GetTimestampAsync(const TMap<FString, FString>& Metadata)
{
    TMap<FString, FString> Headers;
    Headers.Add(TEXT("Authorization"), FString::Printf(TEXT("Bearer %s"), *ApiKey));
    Headers.Add(TEXT("Content-Type"), TEXT("application/json"));

    FString MetadataJson;
    if (!Metadata.IsEmpty())
    {
        TSharedPtr<FJsonObject> JsonObject = MakeShared<FJsonObject>();
        for (const auto& Pair : Metadata)
        {
            JsonObject->SetStringField(Pair.Key, Pair.Value);
        }

        TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&MetadataJson);
        FJsonSerializer::Serialize(JsonObject.ToSharedRef(), Writer);
    }

    SendHttpRequest(TEXT("/timestamp"), TEXT("GET"), Headers, MetadataJson);
}

void ARTCManger::StartTimestampStreaming(int32 IntervalMs)
{
    if (WebSocket.IsValid() && WebSocket->IsConnected())
    {
        UE_LOG(LogTemp, Warning, TEXT("WebSocket already connected"));
        return;
    }

    ConnectWebSocket();
}

void ARTCManger::StopTimestampStreaming()
{
    if (WebSocket.IsValid())
    {
        WebSocket->Close();
        WebSocket.Reset();
    }

    bIsConnected = false;
}

void ARTCManger::SendHttpRequest(const FString& Endpoint, const FString& Method,
                                const TMap<FString, FString>& Headers, const FString& Body)
{
    TSharedRef<IHttpRequest> Request = FHttpModule::Get().CreateRequest();
    Request->SetURL(ServerUrl + Endpoint);
    Request->SetVerb(Method);

    for (const auto& Header : Headers)
    {
        Request->SetHeader(Header.Key, Header.Value);
    }

    if (!Body.IsEmpty())
    {
        Request->SetContentAsString(Body);
    }

    Request->OnProcessRequestComplete().BindUObject(this, &ARTCManger::OnHttpResponseReceived);
    Request->ProcessRequest();
}

void ARTCManger::OnHttpResponseReceived(FHttpRequestPtr Request, FHttpResponsePtr Response,
                                       bool bWasSuccessful)
{
    if (!bWasSuccessful || !Response.IsValid())
    {
        OnError.Broadcast(TEXT("HTTP request failed"));
        return;
    }

    if (Response->GetResponseCode() != 200)
    {
        OnError.Broadcast(FString::Printf(TEXT("HTTP error: %d"), Response->GetResponseCode()));
        return;
    }

    FString ResponseString = Response->GetContentAsString();

    TSharedPtr<FJsonObject> JsonObject;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ResponseString);

    if (!FJsonSerializer::Deserialize(Reader, JsonObject) || !JsonObject.IsValid())
    {
        OnError.Broadcast(TEXT("Failed to parse JSON response"));
        return;
    }

    FRTCTimestamp Timestamp;
    Timestamp.Unix = JsonObject->GetIntegerField(TEXT("unix"));
    Timestamp.Nano = JsonObject->GetIntegerField(TEXT("nano"));
    Timestamp.ISO8601 = JsonObject->GetStringField(TEXT("iso8601"));
    Timestamp.Signature = JsonObject->GetStringField(TEXT("signature"));
    Timestamp.Confidence = JsonObject->GetNumberField(TEXT("confidence"));
    Timestamp.DriftMs = JsonObject->GetNumberField(TEXT("drift_ms"));
    Timestamp.Source = JsonObject->GetStringField(TEXT("source"));

    const TSharedPtr<FJsonObject>* MetadataObject;
    if (JsonObject->TryGetObjectField(TEXT("metadata"), MetadataObject))
    {
        for (const auto& Pair : (*MetadataObject)->Values)
        {
            Timestamp.Metadata.Add(Pair.Key, Pair.Value->AsString());
        }
    }

    if (VerifySignature(Timestamp))
    {
        OnTimestampReceived.Broadcast(Timestamp);
    }
    else
    {
        OnError.Broadcast(TEXT("Invalid timestamp signature"));
    }
}

void ARTCManger::ConnectWebSocket()
{
    FString WsUrl = ServerUrl.Replace(TEXT("https://"), TEXT("wss://"))
                             .Replace(TEXT("http://"), TEXT("ws://"));

    WebSocket = FWebSocketsModule::Get().CreateWebSocket(WsUrl + TEXT("/timestamp/stream?interval=100"),
                                                         TEXT("rtc-protocol"));

    WebSocket->OnConnected().AddUObject(this, &ARTCManger::OnWebSocketConnected);
    WebSocket->OnMessage().AddUObject(this, &ARTCManger::OnWebSocketMessage);
    WebSocket->OnError().AddUObject(this, &ARTCManger::OnWebSocketError);

    TMap<FString, FString> Headers;
    Headers.Add(TEXT("Authorization"), FString::Printf(TEXT("Bearer %s"), *ApiKey));

    WebSocket->Connect(Headers);
}

void ARTCManger::OnWebSocketConnected()
{
    bIsConnected = true;
    UE_LOG(LogTemp, Log, TEXT("RTC WebSocket connected"));
}

void ARTCManger::OnWebSocketMessage(const FString& Message)
{
    // Parse and verify timestamp (similar to HTTP response handling)
    TSharedPtr<FJsonObject> JsonObject;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Message);

    if (FJsonSerializer::Deserialize(Reader, JsonObject) && JsonObject.IsValid())
    {
        FRTCTimestamp Timestamp;
        // Populate timestamp fields...

        if (VerifySignature(Timestamp))
        {
            OnTimestampReceived.Broadcast(Timestamp);
        }
    }
}

void ARTCManger::OnWebSocketError(const FString& Error)
{
    bIsConnected = false;
    OnError.Broadcast(Error);

    // Attempt reconnection
    GetWorld()->GetTimerManager().SetTimer(ReconnectTimerHandle,
        this, &ARTCManger::ConnectWebSocket, 5.0f, false);
}

bool ARTCManger::VerifySignature(const FRTCTimestamp& Timestamp) const
{
    FString Data = FString::Printf(TEXT("%lld:%lld:%s"), Timestamp.Unix, Timestamp.Nano, *Timestamp.Source);
    FString ExpectedSignature = GenerateHMAC(Data, SecretKey);

    return ExpectedSignature.Equals(Timestamp.Signature);
}

FString ARTCManger::GenerateHMAC(const FString& Data, const FString& Key) const
{
    // Use UE5's crypto utilities for HMAC-SHA256
    // This is a simplified implementation - use proper crypto in production
    FSHA256 Hash;
    Hash.Update((uint8*)TCHAR_TO_UTF8(*Key), Key.Len());
    Hash.Update((uint8*)TCHAR_TO_UTF8(*Data), Data.Len());

    uint8 HashResult[32];
    Hash.Final(HashResult);

    FString Result;
    for (int32 i = 0; i < 32; i++)
    {
        Result += FString::Printf(TEXT("%02x"), HashResult[i]);
    }

    return Result;
}
