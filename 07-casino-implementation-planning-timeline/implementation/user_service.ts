// Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

/**
 * User Service - Chapter 22: Casino Implementation Planning and Timeline
 *
 * NestJS user management service handling user creation, authentication,
 * and JWT token generation with bcrypt password hashing.
 *
 * Part of the iGaming Platform Engineering book.
 */

import { Injectable, ConflictException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { User } from './user.entity';
import * as bcrypt from 'bcrypt';
import { JwtService } from '@nestjs/jwt';

// Type definitions (import from DTOs in real implementation)
interface CreateUserDto {
  email: string;
  password: string;
  [key: string]: any;
}

@Injectable()
export class UserService {
  constructor(
    @InjectRepository(User)
    private userRepository: Repository<User>,
    private jwtService: JwtService,
  ) {}

  async createUser(createUserDto: CreateUserDto): Promise<User> {
    const { email, password, ...userData } = createUserDto;

    // Check if user exists
    const existingUser = await this.userRepository.findOne({ where: { email } });
    if (existingUser) {
      throw new ConflictException('User already exists');
    }

    // Hash password
    const saltRounds = 12;
    const hashedPassword = await bcrypt.hash(password, saltRounds);

    // Create user
    const user = this.userRepository.create({
      ...userData,
      email,
      password: hashedPassword,
      isActive: false,
      emailVerified: false,
    });

    return this.userRepository.save(user);
  }

  async validateUser(email: string, password: string): Promise<User | null> {
    const user = await this.userRepository.findOne({
      where: { email, isActive: true }
    });

    if (user && await bcrypt.compare(password, user.password)) {
      return user;
    }

    return null;
  }

  async generateJwtToken(user: User): Promise<string> {
    const payload = {
      sub: user.id,
      email: user.email,
      role: user.role,
    };

    return this.jwtService.sign(payload);
  }
}
