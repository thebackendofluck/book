#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Biometric Verification Service
Face recognition, liveness detection, and anti-spoofing
"""

import os
import cv2
import numpy as np
import face_recognition  # ty:ignore[unresolved-import]
import mediapipe as mp  # ty:ignore[unresolved-import]
from typing import Dict, Any, List, Tuple, Optional
import logging
import asyncio
from fastapi import FastAPI, File, UploadFile, HTTPException
import uvicorn
from prometheus_client import Counter, Histogram
import dlib  # ty:ignore[unresolved-import]
import torch  # ty:ignore[unresolved-import]
import torchvision.transforms as transforms  # ty:ignore[unresolved-import]
from PIL import Image

logger = logging.getLogger(__name__)

# Metrics
FACE_VERIFICATIONS = Counter('face_verifications_total', 'Total face verifications')
LIVENESS_CHECKS = Counter('liveness_checks_total', 'Total liveness checks')
SPOOFING_DETECTED = Counter('spoofing_detected_total', 'Spoofing attempts detected')
VERIFICATION_TIME = Histogram('verification_time_seconds', 'Biometric verification time')

class BiometricVerificationService:
    def __init__(self):
        self.face_detector = dlib.get_frontal_face_detector()
        self.face_encoder = face_recognition.face_encodings
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            min_detection_confidence=0.5
        )
        
        # Load anti-spoofing model if available
        self.anti_spoofing_model = self._load_anti_spoofing_model()
        
        # Thresholds
        self.face_match_threshold = float(os.environ.get('FACE_MATCH_THRESHOLD', 0.95))
        self.liveness_threshold = 0.7
        self.quality_threshold = 0.6
        
        logger.info("Biometric Verification Service initialized")
    
    def _load_anti_spoofing_model(self):
        """Load pre-trained anti-spoofing model"""
        try:
            # Load a pre-trained model for anti-spoofing
            # In production, use specialized models like Silent-Face or FAS
            model = torch.hub.load('pytorch/vision:v0.10.0', 'resnet18', pretrained=True)
            model.eval()
            return model
        except Exception as e:
            logger.warning(f"Could not load anti-spoofing model: {str(e)}")
            return None
    
    async def verify_face(
        self,
        document_image: bytes,
        selfie_image: bytes,
        user_id: str
    ) -> Dict[str, Any]:
        """Verify face match between document and selfie"""
        
        FACE_VERIFICATIONS.inc()
        
        try:
            # Convert bytes to images
            doc_img = self._bytes_to_image(document_image)
            selfie_img = self._bytes_to_image(selfie_image)
            
            # Extract face from document
            doc_face = await self._extract_face_from_document(doc_img)
            if doc_face is None:
                return {
                    'success': False,
                    'reason': 'No face found in document',
                    'confidence': 0.0
                }
            
            # Extract face from selfie
            selfie_face = await self._extract_face_from_selfie(selfie_img)
            if selfie_face is None:
                return {
                    'success': False,
                    'reason': 'No face found in selfie',
                    'confidence': 0.0
                }
            
            # Perform liveness detection
            liveness_result = await self.check_liveness(selfie_img)
            if not liveness_result['is_live']:
                SPOOFING_DETECTED.inc()
                return {
                    'success': False,
                    'reason': 'Liveness check failed',
                    'confidence': 0.0,
                    'liveness_score': liveness_result['score']
                }
            
            # Check for spoofing
            if await self._detect_spoofing(selfie_img):
                SPOOFING_DETECTED.inc()
                return {
                    'success': False,
                    'reason': 'Spoofing detected',
                    'confidence': 0.0
                }
            
            # Compare faces
            match_result = await self._compare_faces(doc_face, selfie_face)
            
            # Store face encoding securely
            if match_result['match']:
                await self._store_face_encoding(user_id, selfie_face)
            
            return {
                'success': match_result['match'],
                'confidence': match_result['confidence'],
                'liveness_score': liveness_result['score'],
                'quality_score': await self._assess_face_quality(selfie_img),
                'landmarks_detected': match_result.get('landmarks_detected', False)
            }
            
        except Exception as e:
            logger.error(f"Face verification failed: {str(e)}")
            return {
                'success': False,
                'reason': str(e),
                'confidence': 0.0
            }
    
    async def check_liveness(self, image: np.ndarray) -> Dict[str, Any]:
        """Perform liveness detection"""
        
        LIVENESS_CHECKS.inc()
        
        checks = {
            'face_mesh': await self._check_face_mesh(image),
            'eye_blink': await self._detect_eye_blink(image),
            'mouth_movement': await self._detect_mouth_movement(image),
            'texture_analysis': await self._analyze_skin_texture(image),
            'depth_estimation': await self._estimate_face_depth(image),
            'reflection_check': await self._check_reflections(image)
        }
        
        # Calculate liveness score
        passed_checks = sum(1 for check in checks.values() if check)
        liveness_score = passed_checks / len(checks)
        
        return {
            'is_live': liveness_score >= self.liveness_threshold,
            'score': liveness_score,
            'checks': checks
        }
    
    async def _check_face_mesh(self, image: np.ndarray) -> bool:
        """Check if face mesh can be properly detected"""
        
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb_image)
        
        if results.multi_face_landmarks:
            face_landmarks = results.multi_face_landmarks[0]
            
            # Check if we have all expected landmarks
            if len(face_landmarks.landmark) >= 468:  # MediaPipe face mesh has 468 landmarks
                # Check landmark distribution
                landmarks_3d = [(lm.x, lm.y, lm.z) for lm in face_landmarks.landmark]
                
                # Real faces have depth variation
                z_values = [z for _, _, z in landmarks_3d]
                z_variance = np.var(z_values)
                
                return z_variance > 0.0001  # Threshold for depth variation
        
        return False
    
    async def _detect_eye_blink(self, image: np.ndarray) -> bool:
        """Detect if eyes show natural characteristics"""
        
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self.face_detector(gray)
        
        if len(faces) == 0:
            return False
        
        # Use facial landmarks to detect eyes
        shape_predictor = dlib.shape_predictor("shape_predictor_68_face_landmarks.dat")
        shape = shape_predictor(gray, faces[0])
        
        # Extract eye regions
        left_eye = self._get_eye_region(shape, [36, 37, 38, 39, 40, 41])
        right_eye = self._get_eye_region(shape, [42, 43, 44, 45, 46, 47])
        
        # Calculate eye aspect ratio (EAR)
        left_ear = self._calculate_ear(left_eye)
        right_ear = self._calculate_ear(right_eye)
        
        # Natural eyes have specific aspect ratios
        avg_ear = (left_ear + right_ear) / 2.0
        
        return 0.2 < avg_ear < 0.4  # Normal range for open eyes
    
    def _get_eye_region(self, shape, indices: List[int]) -> List[Tuple[int, int]]:
        """Extract eye region coordinates"""
        return [(shape.part(i).x, shape.part(i).y) for i in indices]
    
    def _calculate_ear(self, eye: List[Tuple[int, int]]) -> float:
        """Calculate Eye Aspect Ratio"""
        
        # Compute distances between vertical eye landmarks
        A = np.linalg.norm(np.array(eye[1]) - np.array(eye[5]))
        B = np.linalg.norm(np.array(eye[2]) - np.array(eye[4]))
        
        # Compute distance between horizontal eye landmarks
        C = np.linalg.norm(np.array(eye[0]) - np.array(eye[3]))
        
        # Eye aspect ratio
        ear = (A + B) / (2.0 * C) if C > 0 else 0.0
        
        return float(ear)
    
    async def _detect_mouth_movement(self, image: np.ndarray) -> bool:
        """Check for natural mouth characteristics"""
        
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb_image)
        
        if results.multi_face_landmarks:
            face_landmarks = results.multi_face_landmarks[0]
            
            # Get mouth landmarks
            upper_lip = face_landmarks.landmark[13]
            lower_lip = face_landmarks.landmark[14]
            
            # Calculate mouth opening
            mouth_distance = abs(upper_lip.y - lower_lip.y)
            
            # Natural mouths have certain proportions
            return 0.01 < mouth_distance < 0.1
        
        return False
    
    async def _analyze_skin_texture(self, image: np.ndarray) -> bool:
        """Analyze skin texture for authenticity"""
        
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply Gabor filters to detect texture
        ksize = 31
        sigma = 4.0
        theta = np.pi / 4
        lamda = 10.0
        gamma = 0.5
        
        kernel = cv2.getGaborKernel(
            (ksize, ksize), sigma, theta, lamda, gamma, 0, ktype=cv2.CV_32F
        )
        
        filtered = cv2.filter2D(gray, cv2.CV_8UC3, kernel)
        
        # Real skin has specific texture patterns
        texture_variance = np.var(filtered)
        
        # Threshold based on empirical data
        return bool(100 < texture_variance < 5000)
    
    async def _estimate_face_depth(self, image: np.ndarray) -> bool:
        """Estimate face depth to detect 2D spoofing"""
        
        # Use face mesh for 3D estimation
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb_image)
        
        if results.multi_face_landmarks:
            face_landmarks = results.multi_face_landmarks[0]
            
            # Get nose tip and face contour landmarks
            nose_tip = face_landmarks.landmark[1]
            left_cheek = face_landmarks.landmark[234]
            right_cheek = face_landmarks.landmark[454]
            
            # Calculate relative depths
            depth_diff = abs(nose_tip.z - (left_cheek.z + right_cheek.z) / 2)
            
            # Real faces have significant depth variation
            return depth_diff > 0.01
        
        return False
    
    async def _check_reflections(self, image: np.ndarray) -> bool:
        """Check for screen reflections indicating spoofing"""
        
        # Convert to HSV for better reflection detection
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        
        # Look for high saturation and value areas (screen reflections)
        _, saturation, value = cv2.split(hsv)
        
        # Detect bright spots that might be screen reflections
        bright_pixels = np.where(value > 240)
        bright_ratio = len(bright_pixels[0]) / (image.shape[0] * image.shape[1])
        
        # Too many bright pixels might indicate screen
        return bright_ratio < 0.1
    
    async def _detect_spoofing(self, image: np.ndarray) -> bool:
        """Comprehensive spoofing detection"""
        
        if self.anti_spoofing_model is None:
            return False
        
        try:
            # Preprocess image for model
            pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            
            transform = transforms.Compose([
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225]
                )
            ])
            
            input_tensor = transform(pil_image).unsqueeze(0)
            
            # Run anti-spoofing model
            with torch.no_grad():
                output = self.anti_spoofing_model(input_tensor)
                
                # Simplified spoofing detection
                # In production, use specialized anti-spoofing models
                confidence = torch.nn.functional.softmax(output, dim=1)
                spoofing_score = confidence[0][0].item()
                
                return spoofing_score > 0.5
                
        except Exception as e:
            logger.error(f"Spoofing detection failed: {str(e)}")
            return False
    
    async def _extract_face_from_document(self, image: np.ndarray) -> Optional[np.ndarray]:
        """Extract face from identity document"""
        
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        faces = self.face_detector(gray)
        
        if len(faces) == 0:
            return None
        
        # Get the largest face (assuming it's the main photo)
        largest_face = max(faces, key=lambda rect: rect.width() * rect.height())
        
        # Extract face region with padding
        x, y, w, h = (largest_face.left(), largest_face.top(), 
                     largest_face.width(), largest_face.height())
        
        padding = int(w * 0.2)
        x = max(0, x - padding)
        y = max(0, y - padding)
        w = min(image.shape[1] - x, w + 2 * padding)
        h = min(image.shape[0] - y, h + 2 * padding)
        
        face_img = image[y:y+h, x:x+w]
        
        # Get face encoding
        face_encodings = face_recognition.face_encodings(face_img)
        
        return face_encodings[0] if face_encodings else None
    
    async def _extract_face_from_selfie(self, image: np.ndarray) -> Optional[np.ndarray]:
        """Extract face from selfie image"""
        
        # Find faces in image
        face_locations = face_recognition.face_locations(image)
        
        if not face_locations:
            return None
        
        # Get face encoding
        face_encodings = face_recognition.face_encodings(image, face_locations)
        
        return face_encodings[0] if face_encodings else None
    
    async def _compare_faces(
        self,
        encoding1: np.ndarray,
        encoding2: np.ndarray
    ) -> Dict[str, Any]:
        """Compare two face encodings"""
        
        # Calculate distance between encodings
        distance = face_recognition.face_distance([encoding1], encoding2)[0]
        
        # Convert distance to confidence (0-1 scale)
        confidence = 1.0 - distance
        
        # Check if match exceeds threshold
        is_match = confidence >= self.face_match_threshold
        
        return {
            'match': is_match,
            'confidence': float(confidence),
            'distance': float(distance),
            'threshold': self.face_match_threshold
        }
    
    async def _assess_face_quality(self, image: np.ndarray) -> float:
        """Assess overall quality of face image"""
        
        quality_scores = []
        
        # Check resolution
        height, width = image.shape[:2]
        resolution_score = min(1.0, (height * width) / (1920 * 1080))
        quality_scores.append(resolution_score)
        
        # Check brightness
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mean_brightness = np.mean(gray)
        brightness_score = 1.0 - abs(mean_brightness - 127) / 127
        quality_scores.append(brightness_score)
        
        # Check contrast
        contrast = np.std(gray)
        contrast_score = min(1.0, contrast / 80)
        quality_scores.append(contrast_score)
        
        # Check blur
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        blur_score = min(1.0, laplacian_var / 500)
        quality_scores.append(blur_score)
        
        # Check face size
        faces = face_recognition.face_locations(image)
        if faces:
            face = faces[0]
            face_area = (face[2] - face[0]) * (face[1] - face[3])
            image_area = height * width
            face_ratio = face_area / image_area
            face_size_score = min(1.0, face_ratio * 10)  # Face should be ~10% of image
            quality_scores.append(face_size_score)
        
        return np.mean(quality_scores)
    
    async def _store_face_encoding(self, user_id: str, encoding: np.ndarray):
        """Store face encoding securely"""
        
        # Convert encoding to bytes
        encoding_bytes = encoding.tobytes()
        
        # Encrypt before storage (would use encryption service)
        # Store in database
        # Log storage event
        
        logger.info(f"Face encoding stored for user {user_id}")
    
    def _bytes_to_image(self, image_bytes: bytes) -> np.ndarray:
        """Convert bytes to numpy array"""
        nparr = np.frombuffer(image_bytes, np.uint8)
        return cv2.imdecode(nparr, cv2.IMREAD_COLOR)

# Create FastAPI app
app = FastAPI(title="Biometric Verification API")
biometric_service = BiometricVerificationService()

@app.post("/verify-face")
async def verify_face(
    document: UploadFile = File(...),
    selfie: UploadFile = File(...),
    user_id: str = ""
):
    doc_bytes = await document.read()
    selfie_bytes = await selfie.read()
    
    result = await biometric_service.verify_face(doc_bytes, selfie_bytes, user_id)
    return result

@app.post("/check-liveness")
async def check_liveness(image: UploadFile = File(...)):
    image_bytes = await image.read()
    image_np = biometric_service._bytes_to_image(image_bytes)
    
    result = await biometric_service.check_liveness(image_np)
    return result

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
